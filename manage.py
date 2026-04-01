#!/usr/bin/env python
"""OpenOutreach management entrypoint.

Usage:
    python manage.py                          # run the daemon (interactive onboarding)
    python manage.py --onboard config.json    # run the daemon (non-interactive, first run only)
    python manage.py runserver                # Django Admin at http://localhost:8000/admin/
    python manage.py migrate                  # run Django migrations
    python manage.py createsuperuser
"""
import logging
import os
import sys
import warnings

# langchain-openai stores a Pydantic model in a dict-typed field, triggering
# a harmless serialization warning on every structured-output call.
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")

import django
django.setup()

from linkedin.management.setup_crm import setup_crm

logging.getLogger().handlers.clear()
logging.basicConfig(
    level=logging.DEBUG,
    format="%(message)s",
)

# Suppress noisy third-party loggers
for _name in ("urllib3", "httpx", "langchain", "openai", "playwright",
              "httpcore", "fastembed", "huggingface_hub", "filelock",
              "asyncio"):
    logging.getLogger(_name).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _run_daemon(onboard_file=None):
    from linkedin.api.newsletter import ensure_newsletter_subscription
    from linkedin.daemon import run_daemon
    from linkedin.url_utils import public_id_to_url
    from linkedin.setup.gdpr import apply_gdpr_newsletter_override
    from linkedin.onboarding import ensure_onboarding, OnboardConfig
    from linkedin.browser.registry import get_or_create_session
    from linkedin.models import Campaign

    if onboard_file:
        if not Campaign.objects.exists():
            ensure_onboarding(OnboardConfig.from_json(onboard_file))
    else:
        ensure_onboarding()

    from linkedin.conf import LLM_API_KEY
    from linkedin.browser.registry import get_first_active_profile

    if not LLM_API_KEY:
        logger.error("LLM_API_KEY is required. Set it in .env or environment.")
        sys.exit(1)

    linkedin_profile = get_first_active_profile()
    if linkedin_profile is None:
        logger.error("No active LinkedIn profiles found.")
        sys.exit(1)

    session = get_or_create_session(linkedin_profile)

    # Set default campaign (first non-freemium, or first available) for startup tasks
    first_campaign = next((c for c in session.campaigns if not c.is_freemium), None) or session.campaigns[0]
    if first_campaign is None:
        logger.error("No campaigns found for this user.")
        sys.exit(1)
    session.campaign = first_campaign

    profile = session.self_profile

    if not session.linkedin_profile.newsletter_processed:
        country_code = profile.get("country_code")
        apply_gdpr_newsletter_override(session, country_code)
        linkedin_url = public_id_to_url(profile["public_identifier"])
        ensure_newsletter_subscription(session, linkedin_url=linkedin_url)
        session.linkedin_profile.newsletter_processed = True
        session.linkedin_profile.save(update_fields=["newsletter_processed"])

    run_daemon(session)


def _ensure_db():
    from django.core.management import call_command
    call_command("migrate", "--no-input", verbosity=0)
    setup_crm()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # No arguments → run the daemon
        _ensure_db()
        _run_daemon()
    elif sys.argv[1] == "--onboard":
        # --onboard <file> → non-interactive onboard + daemon (Premium)
        if len(sys.argv) < 3:
            sys.exit("Usage: python manage.py --onboard <config.json>")
        _ensure_db()
        _run_daemon(onboard_file=sys.argv[2])
    else:
        if sys.argv[1] == "runserver":
            _ensure_db()
        from django.core.management import execute_from_command_line
        execute_from_command_line(sys.argv)
