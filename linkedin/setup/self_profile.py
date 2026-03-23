# linkedin/self_profile.py
"""Discover and persist the logged-in user's own LinkedIn profile."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

ME_URL = "https://www.linkedin.com/in/me/"


def discover_self_profile(session) -> dict:
    """Fetch the logged-in user's profile via Voyager API and persist as disqualified Leads.

    Creates two disqualified leads: one for the real profile URL (so auto-discovery
    won't re-enrich it) and a ``/in/me/`` marker that caches the full profile for
    lazy accessors.  Neither lead gets an embedding.

    Returns the parsed profile dict.
    Raises ``AuthenticationError`` if the API call fails.
    """
    import json

    from crm.models import Lead
    from linkedin.api.client import PlaywrightLinkedinAPI
    from linkedin.db.urls import public_id_to_url
    from linkedin.exceptions import AuthenticationError

    api = PlaywrightLinkedinAPI(session=session)
    profile, _raw = api.get_profile(public_identifier="me")

    if not profile:
        raise AuthenticationError("Could not fetch own profile via Voyager API")

    real_id = profile["public_identifier"]
    real_url = public_id_to_url(real_id)

    # Disqualified lead for the real profile URL (no embedding).
    Lead.objects.update_or_create(
        linkedin_url=real_url,
        defaults={
            "first_name": profile.get("first_name", ""),
            "last_name": profile.get("last_name", ""),
            "disqualified": True,
            "description": json.dumps(profile, ensure_ascii=False, default=str),
        },
    )
    logger.info("Self-profile discovered: %s", real_url)

    # /in/me/ marker — disqualified, carries the full profile.
    Lead.objects.update_or_create(
        linkedin_url=ME_URL,
        defaults={
            "disqualified": True,
            "public_identifier": real_id,
            "description": json.dumps(profile, ensure_ascii=False, default=str),
        },
    )

    return profile
