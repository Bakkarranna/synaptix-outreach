# linkedin/browser/session.py
from __future__ import annotations

import logging
import random
import time
from functools import cached_property

from linkedin.conf import MIN_DELAY, MAX_DELAY

logger = logging.getLogger(__name__)

# The main LinkedIn auth cookie
_AUTH_COOKIE_NAME = "li_at"


def random_sleep(min_val, max_val):
    delay = random.uniform(min_val, max_val)
    logger.debug(f"Pause: {delay:.2f}s")
    time.sleep(delay)


class AccountSession:
    def __init__(self, handle: str):
        from linkedin.models import LinkedInProfile

        self.handle = handle.strip().lower()

        self.linkedin_profile = LinkedInProfile.objects.select_related(
            "user",
        ).get(user__username=self.handle)
        self.django_user = self.linkedin_profile.user

        # Active campaign — set by the daemon before each lane execution
        self.campaign = None

        # Playwright objects – created on first access or after crash
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

        # Cached after first API lookup (never changes during a session)
        self._self_urn = None

    @cached_property
    def campaigns(self):
        """All campaigns this user belongs to (cached)."""
        from linkedin.models import Campaign
        return list(Campaign.objects.filter(users=self.django_user))

    def ensure_browser(self):
        """Launch or recover browser + login if needed. Call before using .page"""
        from linkedin.browser.login import start_browser_session

        if not self.page or self.page.is_closed():
            logger.debug("Launching/recovering browser for %s", self.handle)
            start_browser_session(session=self, handle=self.handle)
        else:
            self._maybe_refresh_cookies()

    def get_self_urn(self):
        """Lazy accessor: return the authenticated user's fsd_profile URN (cached)."""
        if self._self_urn:
            return self._self_urn

        from crm.models import Lead
        from linkedin.api.client import PlaywrightLinkedinAPI
        from linkedin.exceptions import AuthenticationError
        from linkedin.setup.self_profile import ME_URL

        sentinel = Lead.objects.filter(linkedin_url=ME_URL).only("description", "public_identifier").first()
        if sentinel:
            urn = sentinel.get_urn(self)
            if urn:
                self._self_urn = urn
                return urn

        api = PlaywrightLinkedinAPI(session=self)
        profile, _ = api.get_profile(public_identifier="me")
        if not profile:
            raise AuthenticationError("Cannot fetch own profile via Voyager API")
        self._self_urn = profile["urn"]
        return self._self_urn

    def wait(self, min_delay=MIN_DELAY, max_delay=MAX_DELAY):
        random_sleep(min_delay, max_delay)
        self.page.wait_for_load_state("load")

    def _maybe_refresh_cookies(self):
        """Re-login if the li_at auth cookie in the saved DB state is expired."""
        from linkedin.browser.login import start_browser_session

        self.linkedin_profile.refresh_from_db(fields=["cookie_data"])
        cookie_data = self.linkedin_profile.cookie_data
        if not cookie_data:
            return
        for cookie in cookie_data.get("cookies", []):
            if cookie.get("name") == _AUTH_COOKIE_NAME:
                expires = cookie.get("expires", -1)
                if expires > 0 and expires < time.time():
                    logger.warning("Auth cookie expired for %s — re-authenticating", self.handle)
                    self.close()
                    start_browser_session(session=self, handle=self.handle)
                return

    def close(self):
        if self.context:
            try:
                self.context.close()
                if self.browser:
                    self.browser.close()
                if self.playwright:
                    self.playwright.stop()
                logger.info("Browser closed gracefully (%s)", self.handle)
            except Exception as e:
                logger.debug("Error closing browser: %s", e)
            finally:
                self.page = self.context = self.browser = self.playwright = None

        logger.info("Account session closed → %s", self.handle)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"<AccountSession {self.handle}>"
