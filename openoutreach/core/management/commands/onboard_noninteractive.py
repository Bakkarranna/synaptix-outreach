"""Headless onboarding for a fresh server deploy.

Replays the interactive wizard's DB writes + live credential checks from a JSON
config file, so ``docker compose`` (no TTY) can be fully provisioned on first
boot. Idempotent: a step that is already satisfied — per the wizard's own
``is_done`` checks in ``core/onboarding.STEPS`` — is skipped, so re-running on
every container start never creates a second campaign, mailbox or user.

Usage::

    python manage.py onboard_noninteractive --config /path/to/onboard_config.json

The config path falls back to the ``ONBOARD_CONFIG_PATH`` env var. With neither
(a missing file is fine too) the command prints a note and exits 0, leaving the
daemon to decide — interactive wizard on a TTY, missing-keys exit otherwise.

Schema (every section optional; an absent section is left unsatisfied and the
daemon's own missing-keys gate reports it — only *present-but-broken* config
fails the command, and only live-verification failures exit 1)::

    campaign:            product_docs*, campaign_target*, booking_link, name
    llm:                 model*, api_key*, base_url
    mailbox:             from_address*, password*, smtp_username, host, port,
                         imap_host, imap_port, imap_username, imap_password
    signature:           the per-box sign-off ("" declines; absent = still asked)
    bettercontact_api_key: the mandatory discovery + enrichment key
    firecrawl_api_key:     optional site-scrape finder key
    account:             email*, country*, role ("lead"|"operator"), password,
                         newsletter

``account.password`` is required when the account step runs: on a headless box
this is the only way the Lead gets a dashboard sign-in (the interactive wizard
creates an unusable-password user and the human sets it in the shell). ``role``
defaults to ``lead`` (superuser — can manage the team and config).
"""
import argparse
import json
import os
import sys
from pathlib import Path

from django.core.management.base import BaseCommand


def _config_path(options: dict) -> Path | None:
    env = options.get("config") or os.environ.get("ONBOARD_CONFIG_PATH", "")
    return Path(env).expanduser() if env else None


def _load_config(path: Path | None) -> dict:
    if path is None or not path.exists():
        print(
            f"[onboard] no config at {path or '<none>'} — skipping non-interactive "
            "onboarding (the daemon will decide: interactive on a TTY, missing-keys "
            "exit otherwise)"
        )
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class Command(BaseCommand):
    help = "Run the onboarding steps from a JSON config (idempotent, non-interactive)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--config",
            default="",
            help="Path to onboard_config.json (default: $ONBOARD_CONFIG_PATH).",
        )

    def handle(self, *args, **options) -> None:
        config = _load_config(_config_path(options))
        if not config:
            return

        from openoutreach.core.onboarding import STEPS, missing_keys

        done = {step.key for step in STEPS if step.is_done()}
        failed = False

        # ── 1. Campaign ────────────────────────────────────────────────────
        camp = config.get("campaign")
        if "campaign" in done:
            print("[1] campaign already satisfied — skipping")
        elif not camp:
            print("[1] campaign no config — left unsatisfied")
        else:
            product_docs = (camp.get("product_docs") or "").strip()
            campaign_target = (camp.get("campaign_target") or "").strip()
            if not product_docs or not campaign_target:
                print(
                    "[1] campaign.product_docs and campaign.campaign_target are "
                    "required in onboard_config.json"
                )
                sys.exit(1)
            from openoutreach.core.models import Campaign

            campaign = Campaign.objects.create(
                name=camp.get("name") or "Email Outreach",
                product_docs=product_docs,
                campaign_target=campaign_target,
                booking_link=(camp.get("booking_link") or "").strip(),
            )
            print(f"[1] campaign ok -> pk {campaign.pk}")

        # ── 2. LLM (live-verified, like the wizard) ─────────────────────────
        llm = config.get("llm")
        if "llm" in done:
            print("[2] llm already satisfied — skipping")
        elif not llm:
            print("[2] llm no config — left unsatisfied")
        else:
            model = (llm.get("model") or "").strip()
            api_key = (llm.get("api_key") or "").strip()
            base_url = (llm.get("base_url") or "").strip()
            if not model or not api_key:
                print("[2] llm.model and llm.api_key are required in onboard_config.json")
                sys.exit(1)
            from openoutreach.core.llm import verify_llm_credentials

            error = verify_llm_credentials(model, api_key, base_url)
            if error is not None:
                print(f"[2] LLM VERIFY FAILED: {error}")
                sys.exit(1)
            from openoutreach.core.models import SiteConfig

            cfg = SiteConfig.load()
            cfg.ai_model, cfg.llm_api_key, cfg.llm_api_base = model, api_key, base_url
            cfg.save()
            print(f"[2] llm ok -> {model}")

        # ── 3. Mailbox (SMTP + IMAP auth-checked) + 4. signature ───────────
        box = config.get("mailbox")
        if "mailbox" in done:
            print("[3] mailbox already satisfied — skipping")
        elif not box:
            print("[3] mailbox no config — left unsatisfied")
        else:
            from_address = (box.get("from_address") or "").strip()
            password = (box.get("password") or "").strip()
            if not from_address or not password:
                print(
                    "[3] mailbox.from_address and mailbox.password are required "
                    "in onboard_config.json"
                )
                sys.exit(1)
            from openoutreach.emails.models import Mailbox

            mailbox, reason = Mailbox.objects.create_verified(
                from_address=from_address,
                password=password,
                host=box.get("host") or "smtp.gmail.com",
                port=box.get("port") or 587,
                imap_host=box.get("imap_host") or "imap.gmail.com",
                imap_port=box.get("imap_port") or 993,
                imap_username=box.get("imap_username", "") or "",
                imap_password=box.get("imap_password", "") or "",
                smtp_username=box.get("smtp_username", "") or "",
            )
            if mailbox is None:
                print(f"[3] MAILBOX VERIFY FAILED: {reason}")
                sys.exit(1)
            print(f"[3] mailbox ok -> {mailbox.from_address}")

        signature = config.get("signature")
        if "signature" in done:
            print("[4] signature already satisfied — skipping")
        elif signature is None:
            print("[4] signature no config — left unsatisfied")
        else:
            from openoutreach.emails.models import Mailbox

            for mbox in Mailbox.objects.filter(signature__isnull=True):
                mbox.signature = signature
                mbox.save(update_fields=["signature"])
            print("[4] signature ok" if signature else "[4] signature declined (\"\")")

        # ── 5. BetterContact (mandatory) + optional Firecrawl ──────────────
        bc_key = (config.get("bettercontact_api_key") or "").strip()
        if "bettercontact" in done:
            print("[5] bettercontact already satisfied — skipping")
        elif not bc_key:
            print("[5] bettercontact no config — left unsatisfied")
        else:
            from openoutreach.core.models import SiteConfig

            cfg = SiteConfig.load()
            cfg.bettercontact_api_key = bc_key
            cfg.save()
            print("[5] bettercontact ok")

        fc_key = (config.get("firecrawl_api_key") or "").strip()
        if fc_key:
            from openoutreach.core.models import SiteConfig

            cfg = SiteConfig.load()
            cfg.firecrawl_api_key = fc_key
            cfg.save()
            print("[5b] firecrawl ok")
        else:
            print("[5b] firecrawl skipped (no key — plain-HTTP site finder still active)")

        # ── 6. Account (operator user + country + newsletter) ──────────────
        acct = config.get("account")
        if "account" in done:
            print("[6] account already satisfied — skipping")
        elif not acct or not (acct.get("email") or "").strip() or not (acct.get("country") or "").strip():
            print("[6] account no config (need account.email + account.country) — left unsatisfied")
        else:
            from django.contrib.auth.models import User

            from openoutreach.core.models import SiteConfig
            from openoutreach.emails.models import Mailbox

            email = acct["email"].strip()
            country = (acct.get("country") or "").lower()
            role = (acct.get("role") or "lead").lower()
            password = acct.get("password")

            # Validate everything first so one pass surfaces every config error,
            # before the mailbox prerequisite can mask them.
            if role not in ("lead", "operator"):
                print(f"[6] account.role must be 'lead' or 'operator', got {role!r}")
                sys.exit(1)
            import pytz

            if len(country) != 2 or country.upper() not in pytz.country_timezones:
                print(
                    f"[6] account.country must be a valid ISO 3166 alpha-2 code, got {country!r}"
                )
                sys.exit(1)
            if role == "lead" and not password:
                print(
                    "[6] account.password is required for the Lead so they can "
                    "sign in on a headless deploy"
                )
                sys.exit(1)

            if not Mailbox.objects.exists():
                print("[6] account step requires a mailbox first — left unsatisfied")
                failed = True
            else:
                handle = email.split("@")[0].lower().replace(".", "_").replace("+", "_")
                user, created = User.objects.get_or_create(
                    username=handle,
                    defaults={
                        "is_staff": True,
                        "is_active": True,
                        "email": email,
                    },
                )
                if created:
                    if role == "lead":
                        user.is_superuser = True
                    if password:
                        user.set_password(password)
                    user.save()

                from openoutreach.core.models import Campaign

                campaign = Campaign.objects.first()
                if campaign is not None:
                    campaign.users.add(user)

                cfg = SiteConfig.load()
                cfg.country_code = country
                cfg.save(update_fields=["country_code"])
                print(f"[6] account ok -> {user.username} ({role}, {country.upper()})")

                newsletter = acct.get("newsletter")
                if newsletter is None:  # follow the wizard's jurisdiction default
                    from openoutreach.core.geo import is_gdpr_protected

                    newsletter = not is_gdpr_protected(country)
                if newsletter:
                    from openoutreach.emails.newsletter import subscribe_to_newsletter

                    subscribe_to_newsletter(email)
                    print("[6] newsletter subscribed")

        # ── Summary ────────────────────────────────────────────────────────
        missing = missing_keys()
        if missing:
            print("missing steps:", ", ".join(sorted(missing)))
            if failed:
                print("onboarding incomplete — fix the config and restart the daemon")
        else:
            print("missing steps: NONE — fully onboarded")
