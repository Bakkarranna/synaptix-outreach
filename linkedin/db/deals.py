import json
import logging
from datetime import date

from django.db import transaction
from django.utils import timezone
from termcolor import colored

from linkedin.db._helpers import _make_ticket
from linkedin.db.urls import url_to_public_id, public_id_to_url
from linkedin.enums import ProfileState

logger = logging.getLogger(__name__)

_STATE_LOG_STYLE = {
    ProfileState.QUALIFIED: ("QUALIFIED", "green", []),
    ProfileState.READY_TO_CONNECT: ("READY_TO_CONNECT", "yellow", ["bold"]),
    ProfileState.PENDING: ("PENDING", "cyan", []),
    ProfileState.CONNECTED: ("CONNECTED", "green", ["bold"]),
    ProfileState.COMPLETED: ("COMPLETED", "green", ["bold"]),
    ProfileState.FAILED: ("FAILED", "red", ["bold"]),
}

def parse_metadata(deal) -> dict:
    """Parse deal.metadata as dict, return empty dict on failure."""
    if not deal.metadata:
        return {}
    if isinstance(deal.metadata, dict):
        return deal.metadata
    try:
        return json.loads(deal.metadata)
    except (json.JSONDecodeError, TypeError):
        return {}


def increment_connect_attempts(session, public_id: str) -> int:
    """Increment connect_attempts in deal.metadata and return the new count."""
    from crm.models import Deal

    clean_url = public_id_to_url(public_id)
    dept = session.campaign.department
    deal = Deal.objects.filter(
        lead__website=clean_url, department=dept,
    ).first()
    if not deal:
        return 1

    meta = parse_metadata(deal)
    attempts = meta.get("connect_attempts", 0) + 1
    meta["connect_attempts"] = attempts
    deal.metadata = meta
    deal.save(update_fields=["metadata"])
    return attempts


def _deal_to_profile_dict(deal) -> dict:
    """Convert a Deal (with select_related lead) to a profile dict for lanes."""
    from linkedin.db.leads import lead_to_profile_dict

    base = lead_to_profile_dict(deal.lead)
    base["meta"] = parse_metadata(deal)
    return base


def _deals_at_state(session, state: ProfileState) -> list:
    """Return profile dicts for all Deals at the given state in this campaign's department."""
    from crm.models import Deal

    qs = Deal.objects.filter(
        state=state,
        department=session.campaign.department,
    ).select_related("lead")
    return [_deal_to_profile_dict(d) for d in qs]


def _existing_deal_or_lead(public_id: str, dept):
    """Check for an existing Deal in dept; if none, look up the Lead.

    Returns (lead, existing_deal) — exactly one will be non-None,
    or both None if no Lead exists at all.
    """
    from crm.models import Deal, Lead

    clean_url = public_id_to_url(public_id)
    existing = Deal.objects.filter(lead__website=clean_url, department=dept).first()
    if existing:
        return None, existing
    lead = Lead.objects.filter(website=clean_url).first()
    return lead, None


# ── State transitions ──


def set_profile_state(session, public_identifier: str, new_state: str, reason: str = ""):
    """Move the Deal to the corresponding state.

    Department-scoped: only finds Deals in the current campaign's department.
    Raises ValueError if no Deal exists.
    """
    from crm.models import Deal, ClosingReason

    clean_url = public_id_to_url(public_identifier)
    dept = session.campaign.department
    deal = Deal.objects.filter(lead__website=clean_url, department=dept).first()
    if not deal:
        raise ValueError(f"No Deal for {public_identifier} — cannot set state {new_state}")

    ps = ProfileState(new_state)
    state_changed = (deal.state != ps)

    deal.state = ps
    deal.next_step_date = date.today()

    if reason:
        deal.description = reason

    if ps == ProfileState.FAILED:
        deal.closing_reason = ClosingReason.FAILED
        deal.active = False

    if ps == ProfileState.COMPLETED:
        deal.closing_reason = ClosingReason.COMPLETED
        deal.win_closing_date = timezone.now()

    deal.save()

    label, color, attrs = _STATE_LOG_STYLE.get(ps, ("ERROR", "red", ["bold"]))
    suffix = f" ({reason})" if reason else ""
    if state_changed:
        logger.info("%s %s%s", public_identifier, colored(label, color, attrs=attrs), suffix)
    else:
        logger.debug("%s %s (unchanged)%s", public_identifier, label, suffix)


# ── State queries ──


def get_qualified_profiles(session) -> list:
    return _deals_at_state(session, ProfileState.QUALIFIED)


def get_ready_to_connect_profiles(session) -> list:
    return _deals_at_state(session, ProfileState.READY_TO_CONNECT)


def get_profile_dict_for_public_id(session, public_id: str) -> dict | None:
    """Load profile dict for a single public_id from Deal + Lead (department-scoped)."""
    from crm.models import Deal

    clean_url = public_id_to_url(public_id)
    dept = session.campaign.department
    deal = (
        Deal.objects.filter(lead__website=clean_url, department=dept)
        .select_related("lead")
        .first()
    )
    if not deal:
        return None
    return _deal_to_profile_dict(deal)


# ── Deal creation ──


@transaction.atomic
def create_disqualified_deal(session, public_id: str, reason: str = ""):
    """Create a FAILED Deal with 'Disqualified' closing reason for an LLM-rejected lead.

    LLM qualification rejections are tracked as FAILED Deals (campaign-scoped),
    NOT as Lead.disqualified (which is for permanent account-level exclusion).
    """
    from crm.models import ClosingReason

    dept = session.campaign.department
    lead, existing = _existing_deal_or_lead(public_id, dept)
    if existing:
        return existing
    if not lead:
        logger.warning("create_disqualified_deal: no Lead for %s", public_id)
        return None

    meta = {"reason": reason} if reason else {}
    deal = _create_deal(
        name=f"LinkedIn: {public_id}",
        lead=lead,
        state=ProfileState.FAILED,
        session=session,
        closing_reason=ClosingReason.DISQUALIFIED,
        description=reason,
        active=False,
        metadata=meta,
    )

    suffix = f" ({reason})" if reason else ""
    logger.info("%s %s%s", public_id, colored("DISQUALIFIED", "red", attrs=["bold"]), suffix)
    return deal


@transaction.atomic
def create_freemium_deal(session, public_id: str):
    """Create a Deal in the freemium campaign's department for a candidate lead."""
    dept = session.campaign.department
    lead, existing = _existing_deal_or_lead(public_id, dept)
    if existing:
        return existing
    if not lead:
        raise ValueError(f"No Lead for {public_id}")

    deal = _create_deal(
        name=f"Freemium: {public_id}",
        lead=lead,
        state=ProfileState.QUALIFIED,
        session=session,
        company=lead.company,
    )

    logger.info("%s %s", public_id, colored("FREEMIUM DEAL", "cyan", attrs=["bold"]))
    return deal


def _create_deal(
    *, name, lead, state, session,
    company=None, closing_reason="",
    description="", active=True, metadata=None, next_step_date=None,
):
    """Shared Deal creation with common defaults."""
    from crm.models import Deal

    return Deal.objects.create(
        name=name,
        lead=lead,
        state=state,
        owner=session.django_user,
        department=session.campaign.department,
        company=company,
        closing_reason=closing_reason,
        description=description,
        active=active,
        metadata=metadata or {},
        next_step_date=next_step_date or date.today(),
        ticket=_make_ticket(),
    )
