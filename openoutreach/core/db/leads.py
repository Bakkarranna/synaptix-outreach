import logging

import numpy as np
from django.db import transaction

from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)


@transaction.atomic
def promote_lead_to_deal(campaign, profile_url: str, reason: str = ""):
    """Create a QUALIFIED Deal for a Lead.

    Returns the Deal.
    """
    from openoutreach.crm.models import Lead, Deal

    lead = Lead.objects.filter(profile_url=profile_url).first()
    if not lead:
        raise ValueError(f"No Lead for {profile_url}")

    deal = Deal.objects.create(
        lead=lead,
        campaign=campaign,
        state=DealState.QUALIFIED,
        reason=reason,
    )

    from termcolor import colored
    logger.info("%s %s", profile_url, colored("QUALIFIED", "green", attrs=["bold"]))
    return deal


def create_lead(row: dict, country_code: str = "", discovered_by=None,
                query_terms: str = "", profile_text: str | None = None) -> bool:
    """Persist one Lead Finder row as an embedded Lead awaiting qualification.

    Keyed on ``profile_url`` (the provider's per-person URL). Stamps the firmographic
    ``profile_text`` (the LLM qualifier's input) and the embedding from the same row,
    so qualification never re-fetches anything. ``country_code`` is the ICP's target
    country (Lead Finder rows carry no ISO code) — blank means unknown, which the
    contacts-store geo-gate treats conservatively.

    ``discovered_by`` is the query node that surfaced this row; it lands only on first
    touch (a profile another query already created keeps its original node). Its
    ``query_terms`` are folded into the **embedding only** — not ``profile_text`` — so
    the GP learns which query keywords surface good leads while the LLM judges the
    person on firmographics alone. ``profile_text`` overrides the firmographic build
    (CSV imports may carry their own text); None keeps the default. Returns True when
    a new Lead was created, False when one already existed (idempotent re-discovery).
    """
    from openoutreach.crm.models import Lead
    from openoutreach.discovery import embed_profile, profile_text_for, source_fields_for

    profile_url = row.get("contact_linkedin_profile_url")
    if not profile_url:
        return False

    text = profile_text if profile_text is not None else profile_text_for(row)
    _, created = Lead.objects.get_or_create(
        profile_url=profile_url,
        defaults={
            "embedding": np.asarray(
                embed_profile(text, query_terms), dtype=np.float32).tobytes(),
            "profile_text": text,
            "source_fields": source_fields_for(row),
            "country_code": country_code,
            "discovered_by": discovered_by,
        },
    )
    return created


# ── Import / export (CSV round-trip) ───────────────────────────────


# The portable lead shape: provider-style field names, so an export feeds straight
# back into an import. ``source_fields`` holds most of them; the four Lead-model
# fields are folded in explicitly.
LEAD_EXPORT_FIELDS = [
    "contact_linkedin_profile_url", "email", "country_code",
    "contact_first_name", "contact_last_name", "contact_job_title",
    "contact_headline", "company_name", "contact_industry",
    "company_industry", "contact_location_state", "contact_location_country",
    "contact_seniority", "company_domain", "profile_text", "disqualified",
    "phone", "website",
]

_SOURCE_FIELD_KEYS = [f for f in LEAD_EXPORT_FIELDS if f.startswith("contact_") or f.startswith("company_")]


class LeadImportReport:
    """Counts from one import pass — what landed, what was skipped, and why."""

    def __init__(self):
        self.total = 0          # rows read
        self.created = 0        # new leads persisted (embedded)
        self.skipped_existing = 0  # profile_url already in the store
        self.no_url = 0         # rows with no usable profile URL
        self.no_text = 0        # created leads the LLM can't judge (empty profile_text)

    def __str__(self):
        return (f"imported {self.created} of {self.total} row(s) "
                f"({self.skipped_existing} existing, {self.no_url} no URL, "
                f"{self.no_text} without profile text)")


def lead_to_row(lead) -> dict:
    """One Lead as a portable row dict — the export shape."""
    s = lead.source_fields or {}
    row = {key: s.get(key, "") for key in _SOURCE_FIELD_KEYS}
    row["contact_linkedin_profile_url"] = lead.profile_url
    row["email"] = lead.email or ""
    row["country_code"] = lead.country_code
    row["profile_text"] = lead.profile_text or ""
    row["disqualified"] = "1" if lead.disqualified else "0"
    row["phone"] = lead.phone or ""
    row["website"] = lead.website or ""
    return row


def import_leads_from_rows(rows, default_country: str = "") -> LeadImportReport:
    """Persist CSV rows as embedded Leads; one pass, deduped on profile_url.

    Accepts provider-style columns (``contact_*``/``company_*``) plus three extras:
    ``profile_url`` (alias for the key), ``email`` (stamped on the new lead), and
    ``profile_text`` (raw LLM text; defaults to the firmographic build). Rows whose
    profile URL already exists are skipped; rows with no URL are counted and dropped;
    newly created leads with empty profile text are counted so the operator knows
    which will sit unqualified. The report is the single return value — no partial
    writes on error, each row commits independently.
    """
    from openoutreach.crm.models import Lead
    from openoutreach.discovery import profile_text_for

    report = LeadImportReport()
    for raw in rows:
        report.total += 1
        row = {
            k: (str(v).strip() if v is not None else "")
            for k, v in raw.items() if v is not None and str(v).strip()
        }
        url = row.get("contact_linkedin_profile_url") or row.get("profile_url") or ""
        if not url:
            report.no_url += 1
            continue
        row["contact_linkedin_profile_url"] = url
        country = (row.get("country_code") or default_country or "").strip()[:2].lower()
        raw_text = row.get("profile_text") or ""
        created = create_lead(row, country_code=country,
                              profile_text=raw_text or None)
        if not created:
            report.skipped_existing += 1
            continue
        updates = {}
        for column in ("email", "phone", "website"):
            if row.get(column):
                updates[column] = row[column]
        if updates:
            Lead.objects.filter(profile_url=url).update(**updates)
        if not (raw_text or profile_text_for(row)):
            report.no_text += 1
        report.created += 1
    return report


def disqualify_lead(profile_url: str):
    """Set Lead.disqualified = True (account-level, permanent, cross-campaign)."""
    from openoutreach.crm.models import Lead

    lead = Lead.objects.filter(profile_url=profile_url).first()
    if not lead:
        logger.warning("disqualify_lead: no Lead for %s", profile_url)
        return
    lead.disqualified = True
    lead.save(update_fields=["disqualified"])


def _closed_states() -> tuple:
    """States an unsubscribe leaves alone — the deal is already over.

    Overwriting one would destroy the outcome that closed it, and an opt-out from
    someone whose thread ended weeks ago changes nothing about that thread. Every
    other state moves to UNSUBSCRIBED, whatever leg of the funnel it was on.
    """
    from openoutreach.crm.models import DealState

    return (
        DealState.COMPLETED,
        DealState.FAILED,
        DealState.NO_EMAIL_BETTERCONTACT,
        DealState.UNSUBSCRIBED,
    )


def suppress_email(address: str) -> int:
    """Honour an opt-out from *address*: suppress every lead at it, close their deals.

    Suppression binds to the **person**, not to one thread — so it is written to
    ``Lead.disqualified`` (permanent, account-level, cross-campaign), which the
    candidate queries already filter, rather than to a state only one campaign
    would read. ``Lead.email`` has no unique constraint, so *every* row holding
    the address is suppressed, matched case-insensitively because a mail client
    echoes back whatever casing it was given.

    Returns the number of leads suppressed (0 when the address is unknown — an
    unsubscribe from someone we never emailed, which is not an error).

    Idempotent: re-running over the same address writes the same rows to the same
    values, so a rescanned mailbox costs nothing.
    """
    from openoutreach.crm.models import Deal, DealState, Lead

    address = (address or "").strip()
    if not address:
        return 0

    leads = list(Lead.objects.filter(email__iexact=address))
    if not leads:
        logger.info("unsubscribe from %s matched no lead", address)
        return 0

    Lead.objects.filter(pk__in=[lead.pk for lead in leads]).update(disqualified=True)
    closed = (
        Deal.objects.filter(lead__in=leads)
        .exclude(state__in=_closed_states())
        .update(state=DealState.UNSUBSCRIBED)
    )
    logger.info("unsubscribe from %s: %d lead(s) suppressed, %d deal(s) closed",
                address, len(leads), closed)
    return len(leads)
