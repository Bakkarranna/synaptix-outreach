# linkedin/onboarding.py
"""Onboarding: create Campaign + LinkedInProfile in DB.

Supports two modes:
- Interactive (default): questionary wizard via ``openoutreach`` package.
- Non-interactive: all values supplied via OnboardConfig (CLI flags).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from linkedin.conf import (
    DEFAULT_CONNECT_DAILY_LIMIT,
    DEFAULT_CONNECT_WEEKLY_LIMIT,
    DEFAULT_FOLLOW_UP_DAILY_LIMIT,
    ENV_FILE,
    ROOT_DIR,
)

DEFAULT_PRODUCT_DOCS = ROOT_DIR / "README.md"
DEFAULT_CAMPAIGN_OBJECTIVE = ROOT_DIR / "docs" / "default_campaign.md"

logger = logging.getLogger(__name__)


@dataclass
class OnboardConfig:
    """All values needed to onboard — filled interactively or from CLI flags."""

    linkedin_email: str = ""
    linkedin_password: str = ""
    campaign_name: str = ""
    product_description: str = ""
    campaign_objective: str = ""
    booking_link: str = ""
    seed_urls: str = ""
    llm_api_key: str = ""
    ai_model: str = ""
    llm_api_base: str = ""
    newsletter: bool = True
    connect_daily_limit: int = DEFAULT_CONNECT_DAILY_LIMIT
    connect_weekly_limit: int = DEFAULT_CONNECT_WEEKLY_LIMIT
    follow_up_daily_limit: int = DEFAULT_FOLLOW_UP_DAILY_LIMIT
    legal_acceptance: bool = False

    @classmethod
    def from_json(cls, path: str) -> OnboardConfig:
        import json
        with open(path) as f:
            data = json.load(f)
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_default_file(path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------

def _write_env_var(var_name: str, value: str) -> None:
    """Append a variable to .env if not already present."""
    if ENV_FILE.exists():
        content = ENV_FILE.read_text(encoding="utf-8")
        if var_name not in content:
            with open(ENV_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n{var_name}={value}\n")
    else:
        ENV_FILE.write_text(f"{var_name}={value}\n", encoding="utf-8")


def _set_env_var(var_name: str, value: str) -> None:
    """Write an env var to .env, os.environ, and linkedin.conf."""
    import linkedin.conf as conf

    _write_env_var(var_name, value)
    os.environ[var_name] = value
    setattr(conf, var_name, value)
    logger.info("%s written to %s", var_name, ENV_FILE)


# ---------------------------------------------------------------------------
# Record creation (pure DB, no I/O)
# ---------------------------------------------------------------------------

def _create_campaign(name: str, product_docs: str, objective: str, booking_link: str = ""):
    """Create a Campaign record and return it."""
    from linkedin.models import Campaign

    campaign = Campaign.objects.create(
        name=name,
        product_docs=product_docs,
        campaign_objective=objective,
        booking_link=booking_link,
    )
    logger.info("Created campaign: %s", name)
    print(f"Campaign '{name}' created!")
    return campaign


def _create_account(
    campaign,
    email: str,
    password: str,
    *,
    subscribe: bool = True,
    connect_daily: int = DEFAULT_CONNECT_DAILY_LIMIT,
    connect_weekly: int = DEFAULT_CONNECT_WEEKLY_LIMIT,
    follow_up_daily: int = DEFAULT_FOLLOW_UP_DAILY_LIMIT,
):
    """Create a User + LinkedInProfile record and return the profile."""
    from django.contrib.auth.models import User
    from linkedin.models import LinkedInProfile

    handle = email.split("@")[0].lower().replace(".", "_").replace("+", "_")

    user, created = User.objects.get_or_create(
        username=handle,
        defaults={"is_staff": True, "is_active": True},
    )
    if created:
        user.set_unusable_password()
        user.save()

    campaign.users.add(user)

    profile = LinkedInProfile.objects.create(
        user=user,
        linkedin_username=email,
        linkedin_password=password,
        subscribe_newsletter=subscribe,
        connect_daily_limit=connect_daily,
        connect_weekly_limit=connect_weekly,
        follow_up_daily_limit=follow_up_daily,
    )

    logger.info("Created LinkedIn profile for %s (handle=%s)", email, handle)
    print(f"Account '{handle}' created!")
    return profile


def _auto_accept_legal() -> None:
    """Auto-accept legal for all active profiles that haven't accepted yet."""
    from linkedin.models import LinkedInProfile

    LinkedInProfile.objects.filter(legal_accepted=False, active=True).update(
        legal_accepted=True,
    )


def _create_seed_leads(campaign, seed_urls: str) -> None:
    """Parse seed URL text and create QUALIFIED leads."""
    if not seed_urls or not seed_urls.strip():
        return
    from linkedin.setup.seeds import parse_seed_urls, create_seed_leads

    public_ids = parse_seed_urls(seed_urls)
    if public_ids:
        created = create_seed_leads(campaign, public_ids)
        print(f"{created} seed profile(s) added as QUALIFIED.")


# ---------------------------------------------------------------------------
# Apply wizard answers → DB
# ---------------------------------------------------------------------------

def _apply_answers(answers: dict) -> None:
    """Take a wizard answers dict and create all DB records + write env vars."""
    from linkedin.management.setup_crm import DEFAULT_CAMPAIGN_NAME
    from linkedin.models import Campaign, LinkedInProfile

    # Campaign
    campaign = Campaign.objects.first()
    if campaign is None and "campaign_name" in answers:
        campaign = _create_campaign(
            name=answers.get("campaign_name") or DEFAULT_CAMPAIGN_NAME,
            product_docs=answers.get("product_description", ""),
            objective=answers.get("campaign_objective", ""),
            booking_link=answers.get("booking_link", ""),
        )
        _create_seed_leads(campaign, answers.get("seed_urls", ""))

    # Account
    if (
        not LinkedInProfile.objects.filter(active=True).exists()
        and "linkedin_email" in answers
    ):
        _create_account(
            campaign,
            answers["linkedin_email"],
            answers["linkedin_password"],
            subscribe=answers.get("newsletter", True),
            connect_daily=answers.get("connect_daily_limit", DEFAULT_CONNECT_DAILY_LIMIT),
            connect_weekly=answers.get("connect_weekly_limit", DEFAULT_CONNECT_WEEKLY_LIMIT),
            follow_up_daily=answers.get("follow_up_daily_limit", DEFAULT_FOLLOW_UP_DAILY_LIMIT),
        )

    # LLM env vars
    for var, key in [
        ("LLM_API_KEY", "llm_api_key"),
        ("AI_MODEL", "ai_model"),
        ("LLM_API_BASE", "llm_api_base"),
    ]:
        val = answers.get(key)
        if val:
            _set_env_var(var, val)

    # Legal
    if answers.get("legal_acceptance"):
        _auto_accept_legal()


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------

def _onboard_non_interactive(config: OnboardConfig) -> None:
    """Non-interactive onboarding: create all records from pre-filled config."""
    from linkedin.management.setup_crm import DEFAULT_CAMPAIGN_NAME
    from linkedin.models import Campaign, LinkedInProfile

    # Campaign
    campaign = Campaign.objects.first()
    if campaign is None:
        campaign = _create_campaign(
            name=config.campaign_name or DEFAULT_CAMPAIGN_NAME,
            product_docs=config.product_description or _read_default_file(DEFAULT_PRODUCT_DOCS),
            objective=config.campaign_objective or _read_default_file(DEFAULT_CAMPAIGN_OBJECTIVE),
            booking_link=config.booking_link,
        )
        _create_seed_leads(campaign, config.seed_urls)

    # Account
    if not LinkedInProfile.objects.filter(active=True).exists():
        _create_account(
            campaign, config.linkedin_email, config.linkedin_password,
            subscribe=config.newsletter,
            connect_daily=config.connect_daily_limit,
            connect_weekly=config.connect_weekly_limit,
            follow_up_daily=config.follow_up_daily_limit,
        )

    # LLM env vars
    for var, val in [
        ("LLM_API_KEY", config.llm_api_key),
        ("AI_MODEL", config.ai_model),
        ("LLM_API_BASE", config.llm_api_base),
    ]:
        if val:
            _set_env_var(var, val)

    # Auto-accept legal
    if config.legal_acceptance:
        _auto_accept_legal()


def _onboard_interactive() -> None:
    """Interactive onboarding: questionary wizard from the openoutreach package."""
    import linkedin.conf as conf
    from linkedin.models import Campaign, LinkedInProfile
    from openoutreach.prompts import SELF_HOSTED_QUESTIONS
    from openoutreach.wizard import ask

    # Filter out steps that are already complete
    skip_keys: set[str] = set()

    if Campaign.objects.exists():
        skip_keys |= {
            "campaign_name", "product_description", "campaign_objective",
            "booking_link", "seed_urls",
        }

    if LinkedInProfile.objects.filter(active=True).exists():
        skip_keys |= {
            "linkedin_email", "linkedin_password", "newsletter",
            "connect_daily_limit", "connect_weekly_limit", "follow_up_daily_limit",
            "legal_acceptance",
        }

    if getattr(conf, "LLM_API_KEY", None):
        skip_keys.add("llm_api_key")
    if getattr(conf, "AI_MODEL", None):
        skip_keys.add("ai_model")
    if getattr(conf, "LLM_API_BASE", None):
        skip_keys.add("llm_api_base")

    questions = [q for q in SELF_HOSTED_QUESTIONS if q.key not in skip_keys]
    if not questions or not any(q.required for q in questions):
        return

    answers = ask(questions)
    if answers is None:
        raise SystemExit("Onboarding cancelled.")

    _apply_answers(answers)


def ensure_onboarding(config: OnboardConfig | None = None) -> None:
    """Ensure Campaign, LinkedInProfile, LLM config, and legal acceptance.

    Pass an OnboardConfig to skip interactive prompts (non-interactive mode).
    Pass None (default) for the interactive wizard.
    """
    if config:
        _onboard_non_interactive(config)
    else:
        _onboard_interactive()
