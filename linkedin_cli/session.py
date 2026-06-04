"""The session contract every linkedin_cli verb runs against.

linkedin_cli owns no browser lifecycle and no persistence. Each verb is handed
a *session* — an object that exposes a live Playwright page/context plus a few
lifecycle hooks — and drives LinkedIn through it. The concrete session is the
caller's job: OpenOutreach's daemon backs it with its Django ``AccountSession``;
the standalone CLI backs it with a Playwright CLI session adapter.

``LinkedInSession`` is the typed boundary between the two — it lists exactly what
the platform code touches, and nothing about campaigns, leads, or the DB.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

from playwright.sync_api import BrowserContext, Page, sync_playwright

logger = logging.getLogger(__name__)


@runtime_checkable
class LinkedInSession(Protocol):
    """Browser session a linkedin_cli verb attaches to.

    Implementations own browser launch, the persistent profile, auth/cookies,
    and fingerprint — none of which live here. The verbs only ever read
    ``page``/``context``, resolve their own identity via ``self_profile``, and
    call the lifecycle hooks below.
    """

    #: Live Playwright page for the authenticated session.
    page: Page
    #: Browser context owning the page (cookies, response listeners, storage).
    context: BrowserContext

    @property
    def self_profile(self) -> dict:
        """The logged-in member's own profile dict (the messaging mailbox).

        Resolved once and kept warm for the session; carries at least
        ``urn``, ``first_name``, ``last_name``.
        """
        ...

    def ensure_browser(self) -> None:
        """Launch or recover the browser so ``page`` is usable. Idempotent."""
        ...

    def wait(self, min_delay: float = ..., max_delay: float = ...) -> None:
        """Human-paced pause, then block until the page reaches DOM-ready."""
        ...

    def close(self) -> None:
        """Release browser resources held by the session."""
        ...


# ── Session registry ──────────────────────────────────────────────
#
# The launcher (``linkedin-cli session open``) owns the bound browser and
# records its websocket endpoint here; verb processes look it up by name. This
# is the only on-disk state linkedin_cli keeps — a pointer to a running browser,
# not auth/cookies (those live in the launcher's persistent profile).

def linkedin_cli_home() -> Path:
    """Root dir for linkedin-cli's on-disk state (override via $LINKEDIN_CLI_HOME)."""
    return Path(os.environ.get("LINKEDIN_CLI_HOME") or Path.home() / ".linkedin-cli")


def _sessions_dir() -> Path:
    return linkedin_cli_home() / "sessions"


def _session_file(name: str) -> Path:
    return _sessions_dir() / f"{name}.json"


def write_session(name: str, endpoint: str, pid: int) -> Path:
    """Record a bound browser's endpoint + launcher pid under *name* (atomic)."""
    path = _session_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"name": name, "endpoint": endpoint, "pid": pid}))
    tmp.replace(path)
    return path


def read_session(name: str) -> dict | None:
    """Return the recorded ``{name, endpoint, pid}`` for *name*, or None."""
    path = _session_file(name)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def clear_session(name: str) -> None:
    """Remove the registry entry for *name* if present."""
    _session_file(name).unlink(missing_ok=True)


# ── Playwright-CLI-backed session (connect to a bound browser) ─────

class PlaywrightCliSession:
    """A `LinkedInSession` that drives a launcher-owned bound browser over `connect`.

    The launcher (`linkedin-cli session open`) launches the persistent browser
    and `browser.bind()`s it; this attaches with `chromium.connect(endpoint)`,
    yielding a real `page`/`context` shared with the launcher (and with any
    `playwright-cli attach`). It owns no browser lifecycle and no persistence —
    `close()` only disconnects this client; the launcher's browser keeps running.

    Pacing (``min_pace``/``max_pace``) is injected by the caller (the CLI), not
    read from config here.
    """

    def __init__(self, endpoint: str, *, min_pace: float, max_pace: float,
                 username: str | None = None, password: str | None = None,
                 name: str | None = None):
        self.endpoint = endpoint
        self.min_pace = min_pace
        self.max_pace = max_pace
        self.username = username
        self.password = password
        self.name = name
        self.page = None
        self.context = None
        self._playwright = None
        self._browser = None
        self._self_profile = None

    def ensure_browser(self) -> None:
        if self.page is not None and not self.page.is_closed():
            return
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect(self.endpoint)
        self.context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        logger.debug("Connected to bound browser at %s", self.endpoint)

    @property
    def self_profile(self) -> dict:
        if self._self_profile is None:
            from linkedin_cli.setup.self_profile import discover_self_profile
            self._self_profile = discover_self_profile(self)
        return self._self_profile

    def wait(self, min_delay: float | None = None, max_delay: float | None = None) -> None:
        time.sleep(random.uniform(min_delay or self.min_pace, max_delay or self.max_pace))
        if self.page:
            self.page.wait_for_load_state("domcontentloaded")

    def close(self) -> None:
        # Disconnect this client only — the launcher owns the browser/profile.
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        finally:
            self.page = self.context = self._browser = self._playwright = None

    def __repr__(self) -> str:
        return f"linkedin-cli-session:{self.name or self.endpoint}"
