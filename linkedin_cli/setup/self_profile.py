"""Discover the logged-in member's own LinkedIn profile (the messaging mailbox)."""
from __future__ import annotations

import logging

from linkedin_cli.api.client import PlaywrightLinkedinAPI
from linkedin_cli.exceptions import AuthenticationError

logger = logging.getLogger(__name__)


def discover_self_profile(session) -> dict:
    """Scrape the logged-in member's own profile via Voyager (``me``).

    Pure platform read — no persistence. Returns the parsed profile dict,
    which carries at least ``public_identifier``, ``urn``, and ``full_name``.
    Raises ``AuthenticationError`` if the API call fails (expired/blocked session).
    """
    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session=session)
    profile, _raw = api.get_profile(public_identifier="me")
    if not profile:
        raise AuthenticationError("Could not fetch own profile via Voyager API")
    logger.info("Self-profile discovered: %s", profile.get("public_identifier"))
    return profile
