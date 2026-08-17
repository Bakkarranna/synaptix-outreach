# openoutreach/dashboard/views.py
"""The Synaptix Studio Console — the operator's control panel.

Pages render server-side (no frontend build); controls are small POST endpoints
that redirect back to the page that issued them, and two small JSON endpoints
power the live bits (the daemon pill, and the expandable conversation threads
on the Deals page).

Roles:
- **Lead** (superuser) — everything: configuration, the team, pipeline controls.
- **Operator** (staff) — runs the pipeline: start/stop/nudge the daemon, read
  mail, pause a mailbox, suppress a lead. Cannot change config or manage the team.

The dashboard can stop the daemon and suppress leads, so every page here is
staff-only: it must never answer to an anonymous browser.
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.models import User
from django.db.models import Count, F, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from openoutreach.crm.models import Deal, DealState, Lead
from openoutreach.emails.models import Mailbox, SendVerdict
from openoutreach.core.conf import CAMPAIGN_CONFIG, QUALIFICATION_PROMPT_LOOSENED_SINCE
from openoutreach.core.models import Campaign, SiteConfig

# The spend gate between QUALIFIED and READY_TO_FIND_EMAIL — shown next to every
# stored GP score so a number reads as “passed” or “failed” at a glance. Read from
# the same config the pipeline reads, so the display cannot drift from the gate.
GP_THRESHOLD = CAMPAIGN_CONFIG["min_gp_confidence"]

logger = logging.getLogger(__name__)

# The funnel in reading order — every non-terminal state, then the terminals.
FUNNEL_STATES = [
    DealState.QUALIFIED,
    DealState.READY_TO_FIND_EMAIL,
    DealState.FINDING_EMAIL,
    DealState.READY_TO_EMAIL,
    DealState.EMAILED,
]
TERMINAL_STATES = [
    DealState.NO_EMAIL_BETTERCONTACT,
    DealState.UNSUBSCRIBED,
    DealState.COMPLETED,
    DealState.FAILED,
]

PAGE_SIZE = 50
# Dashboard imports embed each lead (CPU-bound) inside a request — cap the
# upload; bulk imports go through ``manage.py import_leads`` (same code path).
LEADS_IMPORT_MAX_ROWS = 1000


def _mask(secret: str) -> str:
    """A secret's shape, not its content — first 4 chars + length."""
    if not secret:
        return ""
    return f"{secret[:4]}…({len(secret)} chars)"


def lead_required(view):
    """Staff + superuser: the Lead. Operators get sent back to the overview."""

    @wraps(view)
    @staff_member_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, "Only the studio lead can change configuration and manage the team.")
            return redirect("dashboard-overview")
        return view(request, *args, **kwargs)

    return wrapper


# ── Shared payload ────────────────────────────────────────────


def _overview_payload():
    """Everything the overview needs — reused by the JSON status endpoint."""
    from openoutreach.chat.models import ChatMessage

    counts = dict(
        Deal.objects.values_list("state").annotate(n=Count("state"))
        .values_list("state", "n"),
    )
    funnel = [{"state": s, "label": s.label, "count": counts.get(s, 0)} for s in FUNNEL_STATES]
    terminals = [
        {"state": s, "label": s.label, "count": counts.get(s, 0)} for s in TERMINAL_STATES
    ]
    funnel_max = max((row["count"] for row in funnel), default=1) or 1

    boxes = []
    for box in Mailbox.objects.order_by("pk"):
        boxes.append({
            "box": box,
            "headroom": box.headroom_today(),
            "sent_today": box.sent_today(),
            "paused": box.paused_today(),
            "manual_pause": box.send_paused,
            "imap_set": bool(box.imap_username),
            "next_send_at": box.next_send_at,
        })

    campaigns = [
        {
            "campaign": c,
            "phase": f"cold ({len(c.anchor_profiles or [])} anchors)"
                     if c.anchor_profiles else "learning",
            "frontier": c.query_nodes.filter(state="frontier").count(),
            "active_deals": c.deals.exclude(state__in=[s for s in TERMINAL_STATES]).count(),
        }
        for c in Campaign.objects.order_by("pk")
    ]

    config = SiteConfig.load()
    return {
        "funnel": funnel,
        "funnel_max": funnel_max,
        "terminals": terminals,
        "total_leads": Lead.objects.count(),
        "total_deals": Deal.objects.count(),
        "sent_messages": ChatMessage.objects.filter(is_outgoing=True).count(),
        "unanswered": ChatMessage.objects.filter(is_outgoing=False)
            .filter(deal__state=DealState.EMAILED).count(),
        "boxes": boxes,
        "campaigns": campaigns,
        "llm_set": bool(config.ai_model and config.llm_api_key),
        "bettercontact_set": bool(config.bettercontact_api_key),
        "firecrawl_set": bool(config.firecrawl_api_key),
        "country": config.country_code or "",
        "daemon_status": _daemon_status(),
        "daemon_running": _daemon().is_running(),
        "log_tail": _daemon_log_tail(max_chars=1400),
    }


def _box_dict(box_row) -> dict:
    box = box_row["box"]
    return {
        "pk": box.pk,
        "from_address": box.from_address,
        "host": box.host,
        "port": box.port,
        "imap_set": box_row["imap_set"],
        "paused": box_row["paused"],
        "manual_pause": box_row["manual_pause"],
        "daily_limit": box.daily_limit,
        "sent_today": box_row["sent_today"],
        "headroom": box_row["headroom"],
        "next_send_at": box_row["next_send_at"].isoformat() if box_row["next_send_at"] else None,
    }


def _sends_per_day(days: int = 14) -> list[dict]:
    """Outgoing messages per day over the trailing window (calendar days)."""
    from openoutreach.chat.models import ChatMessage

    start = timezone.now().date() - timedelta(days=days - 1)
    rows = (
        ChatMessage.objects
        .filter(is_outgoing=True, creation_date__date__gte=start)
        .annotate(day=TruncDate("creation_date"))
        .values("day").annotate(n=Count("id")).order_by("day")
    )
    by_day = {row["day"]: row["n"] for row in rows}
    return [
        {"label": d.strftime("%b %d"), "n": by_day.get(d, 0)}
        for d in (start + timedelta(days=i) for i in range(days))
    ]


def _qualification_per_day(days: int = 14) -> tuple[list[dict], dict, dict]:
    """LLM qualification verdicts per calendar day over the trailing window.

    Returns (days, agg_7, agg_14): each day carries accepted/rejected counts,
    the verdict rate, and the pixel geometry for a stacked bar. Only the
    LLM-qualified campaign is counted (``campaign__is_freemium=False``) —
    freemium deals are created directly, never judged, so including them would
    inflate the accept rate with rows that were never qualified.

    The label is the deal's current state bucketed by ``creation_date`` — the
    moment the verdict was written (``promote_lead_to_deal`` /
    ``create_disqualified_deal`` both create their row at verdict time):
    ``FAILED + wrong_fit`` is a rejection, every other state an acceptance.
    A manual Reviews-page override moves a deal from the reject to the accept
    bucket — the funnel's real outcome, which is exactly what to measure here.
    """
    from openoutreach.crm.models import Deal, Outcome

    start = timezone.now().date() - timedelta(days=days - 1)
    rows = (
        Deal.objects
        .filter(campaign__is_freemium=False, creation_date__date__gte=start)
        .values_list("creation_date__date", "state", "outcome")
    )
    buckets: dict = {}
    for day, state, outcome in rows:
        b = buckets.setdefault(day, {"accepted": 0, "rejected": 0})
        if state == DealState.FAILED and outcome == Outcome.WRONG_FIT:
            b["rejected"] += 1
        else:
            b["accepted"] += 1

    days_out: list[dict] = []
    for i in range(days):
        d = start + timedelta(days=i)
        b = buckets.get(d, {"accepted": 0, "rejected": 0})
        total = b["accepted"] + b["rejected"]
        days_out.append({
            "label": d.strftime("%b %d"),
            "accepted": b["accepted"],
            "rejected": b["rejected"],
            "total": total,
            "rate": (100 * b["accepted"] / total) if total else None,
            "ok_pct": (100 * b["accepted"] / total) if total else 0,
            "no_pct": (100 * b["rejected"] / total) if total else 0,
            "height": total,  # scaled by the template against the window max
        })

    def _agg(n: int) -> dict:
        sub = [b for b in days_out[-n:]]
        acc = sum(b["accepted"] for b in sub)
        rej = sum(b["rejected"] for b in sub)
        return {
            "accepted": acc, "rejected": rej, "total": acc + rej,
            "rate": (100 * acc / (acc + rej)) if (acc + rej) else None,
        }

    return days_out, _agg(7), _agg(days)


def _spark(values: list[int], w: int = 96, h: int = 30, pad: int = 3) -> str:
    """An SVG polyline path for a small sparkline."""
    if not values:
        return ""
    mx = max(values) or 1
    step = (w - 2 * pad) / max(len(values) - 1, 1)
    pts = []
    for i, v in enumerate(values):
        x = pad + i * step
        y = h - pad - (v / mx) * (h - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


# ── Pages ─────────────────────────────────────────────────────


@staff_member_required
def overview(request):
    payload = _overview_payload()

    from openoutreach.chat.models import ChatMessage

    sends = _sends_per_day(14)
    qa_days, qa_7, qa_14 = _qualification_per_day(14)
    qa_max = max((b["height"] for b in qa_days), default=1) or 1
    context = {
        **payload,
        "spark_values": [row["n"] for row in sends],
        "spark_line": _spark([row["n"] for row in sends]),
        "spark_area": _spark_area([row["n"] for row in sends]),
        "spark_labels": [row["label"] for row in sends],
        "qa_days": qa_days,
        "qa_max": qa_max,
        "qa_7": qa_7,
        "qa_14": qa_14,
        "qual_prompt_since": QUALIFICATION_PROMPT_LOOSENED_SINCE,
        "recent_verdicts": SendVerdict.objects.select_related("mailbox")
            .order_by("-created_at")[:10],
        "recent_threads": ChatMessage.objects.select_related("deal", "deal__lead")
            .order_by("-creation_date")[:10],
    }
    return render(request, "dashboard/overview.html", context)


@staff_member_required
def leads(request):
    q = request.GET.get("q", "").strip()
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    queryset = Lead.objects.order_by("-creation_date")
    if q:
        queryset = queryset.filter(
            Q(profile_text__icontains=q)
            | Q(email__icontains=q)
            | Q(profile_url__icontains=q)
        )
    total = queryset.count()
    rows = list(queryset[(page - 1) * PAGE_SIZE:page * PAGE_SIZE])

    # Deal state per lead, latest deal first — the lead's position in the funnel.
    deals = Deal.objects.filter(lead__in=[r.pk for r in rows]).order_by("-creation_date")
    by_lead: dict[int, list] = {}
    for d in deals:
        by_lead.setdefault(d.lead_id, []).append(d)

    return render(request, "dashboard/leads.html", {
        "rows": [(r, by_lead.get(r.pk, [])) for r in rows],
        "q": q,
        "page": page,
        "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        "total": total,
    })


@staff_member_required
def deals(request):
    state = request.GET.get("state", "")
    gp = request.GET.get("gp", "")       # above / below / scored / unscored
    sort = request.GET.get("sort", "")   # score / score-asc (default: recently updated)
    queryset = Deal.objects.select_related("lead", "campaign", "mailbox")
    if state:
        queryset = queryset.filter(state=state)
    if gp == "above":
        queryset = queryset.filter(gp_score__gte=GP_THRESHOLD)
    elif gp == "below":
        queryset = queryset.filter(gp_score__lt=GP_THRESHOLD)
    elif gp == "scored":
        queryset = queryset.filter(gp_score__isnull=False)
    elif gp == "unscored":
        queryset = queryset.filter(gp_score__isnull=True)
    if sort == "score":
        queryset = queryset.order_by(F("gp_score").desc(nulls_last=True))
    elif sort == "score-asc":
        queryset = queryset.order_by(F("gp_score").asc(nulls_last=True))
    else:
        queryset = queryset.order_by("-update_date")
    rows = queryset[:PAGE_SIZE]
    return render(request, "dashboard/deals.html", {
        "rows": rows,
        "state": state,
        "gp": gp,
        "sort": sort,
        "states": list(DealState.choices),
        "total": queryset.count(),
        "gp_threshold": GP_THRESHOLD,
    })


@staff_member_required
def leads_export(request):
    """CSV export of every lead — the ``import_leads`` round-trip shape.

    Streams so the export never needs the whole store in memory; a UTF-8 BOM is
    prepended so spreadsheet apps decode the French accented text correctly.
    """
    import csv

    from django.http import StreamingHttpResponse

    from openoutreach.crm.models import Lead
    from openoutreach.core.db.leads import LEAD_EXPORT_FIELDS, lead_to_row

    qs = Lead.objects.order_by("-creation_date")

    class _Echo:
        def write(self, value):
            return value

    def stream():
        yield "\ufeff"  # UTF-8 BOM — spreadsheets detect the encoding from it
        w = csv.writer(_Echo())
        yield w.writerow(LEAD_EXPORT_FIELDS)
        for lead in qs.iterator():
            row = lead_to_row(lead)
            yield w.writerow([row[f] for f in LEAD_EXPORT_FIELDS])

    response = StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="leads-{timezone.now():%Y%m%d}.csv"')
    return response


@staff_member_required
@require_POST
def leads_import(request):
    """Upload a leads CSV (the export schema) and persist it as embedded Leads.

    Capped at ``LEADS_IMPORT_MAX_ROWS`` rows — the embedding step is CPU-bound,
    so bulk imports beyond the cap should use ``manage.py import_leads``, which
    streams the same code path without a request timeout. Rows whose profile URL
    already exists are skipped; the message reports exactly what happened.
    """
    import csv
    import io

    from openoutreach.core.db.leads import import_leads_from_rows

    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Choose a CSV file to import.")
        return redirect("dashboard-leads")
    try:
        text = io.TextIOWrapper(f.file, encoding="utf-8-sig")
        rows = list(csv.DictReader(text))
    except Exception as e:
        messages.error(request, f"Could not read the file as CSV: {e}")
        return redirect("dashboard-leads")
    if not rows:
        messages.error(request, "The file has no data rows.")
        return redirect("dashboard-leads")
    if len(rows) > LEADS_IMPORT_MAX_ROWS:
        messages.error(
            request,
            f"{len(rows)} rows is over the {LEADS_IMPORT_MAX_ROWS}-row dashboard "
            f"limit — use `manage.py import_leads {f.name}` for the full file.",
        )
        return redirect("dashboard-leads")

    report = import_leads_from_rows(rows)
    msg = str(report)
    if report.no_text:
        msg += f" — {report.no_text} have no profile text and will sit unqualified."
    messages.success(request, msg)
    return redirect("dashboard-leads")


@lead_required
def config_view(request):
    config = SiteConfig.load()
    campaign = Campaign.objects.filter(is_freemium=False).order_by("pk").first()
    boxes = list(Mailbox.objects.order_by("pk"))
    return render(request, "dashboard/config.html", {
        "config": config,
        "campaign": campaign,
        "boxes": boxes,
        "campaigns": list(Campaign.objects.order_by("pk")),
    })


@lead_required
def team(request):
    users = []
    for user in User.objects.filter(is_staff=True).order_by("pk"):
        users.append({
            "user": user,
            "role": "Lead" if user.is_superuser else "Operator",
            "campaign_count": user.campaigns.count(),
            "last_active": user.last_login,
        })
    return render(request, "dashboard/team.html", {
        "users": users,
        "total_staff": User.objects.filter(is_staff=True).count(),
        "total_operators": User.objects.filter(is_staff=True, is_superuser=False).count(),
    })


@staff_member_required
def logs(request):
    return render(request, "dashboard/logs.html", {
        "log": _daemon_log_tail(max_chars=20000),
    })


# ── Pipeline pages — the operator's window onto each agent ─────


@staff_member_required
def outbox(request):
    """The writer's output: the send queue and every composed message.

    ``READY_TO_EMAIL`` deals are what the writer will compose next; the message
    table is what it has already composed — every outgoing ChatMessage (the
    opener, with subject + body) and every inbound one (a lead's reply), in
    conversation order.
    """
    from openoutreach.chat.models import ChatMessage

    campaign = request.GET.get("campaign", "")
    direction = request.GET.get("direction", "")
    q = request.GET.get("q", "").strip()
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    campaigns = list(Campaign.objects.order_by("pk"))

    queue = (
        Deal.objects.filter(state=DealState.READY_TO_EMAIL)
        .filter(lead__disqualified=False)
        .select_related("lead", "campaign", "mailbox")
        .order_by("creation_date")
    )
    if campaign:
        queue = queue.filter(campaign_id=campaign)
    if q:
        # Same search as the message list, adapted to the queue: a queued deal has
        # no composed subject/body yet, so match the lead's firmographics and email
        # (profile_text is the flattened profile the researcher saw).
        queue = queue.filter(
            Q(lead__email__icontains=q)
            | Q(lead__profile_url__icontains=q)
            | Q(lead__profile_text__icontains=q)
            | Q(campaign__name__icontains=q)
        )

    msgs = ChatMessage.objects.select_related(
        "deal", "deal__lead", "deal__campaign", "deal__mailbox",
    ).order_by("-creation_date")
    if campaign:
        msgs = msgs.filter(deal__campaign_id=campaign)
    if direction == "out":
        msgs = msgs.filter(is_outgoing=True)
    elif direction == "in":
        msgs = msgs.filter(is_outgoing=False)
    if q:
        msgs = msgs.filter(
            Q(content__icontains=q)
            | Q(deal__lead__email__icontains=q)
            | Q(deal__lead__profile_url__icontains=q)
            | Q(deal__email_subject__icontains=q)
            | Q(deal__campaign__name__icontains=q)
        )
    total = msgs.count()
    msgs = list(msgs[(page - 1) * PAGE_SIZE:page * PAGE_SIZE])

    querystring = request.GET.copy()
    querystring.pop("page", None)
    return render(request, "dashboard/outbox.html", {
        "queue": queue,
        "queue_count": queue.count(),
        "messages": msgs,
        "campaigns": campaigns,
        "campaign": campaign,
        "direction": direction,
        "q": q,
        "page": page,
        "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        "total": total,
        "querystring": querystring.urlencode(),
    })


@staff_member_required
@staff_member_required
def reviews(request):
    """The qualifier's rejections: every FAILED wrong_fit Deal with the LLM's reason.

    This is the operator's override point — one click requalifies a lead the LLM
    rejected, flipping its Deal back to QUALIFIED (the GP's next refit then labels
    it a fit positive, so the model unlearns the mistaken rejection) and putting it
    back into the rank gate. ``reason`` is kept on the Deal as the audit trail.
    """
    from openoutreach.crm.models import Deal, Outcome

    campaign = request.GET.get("campaign", "")
    q = request.GET.get("q", "").strip()
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    campaigns = list(Campaign.objects.order_by("pk"))

    base = Deal.objects.filter(
        state=DealState.FAILED,
        outcome=Outcome.WRONG_FIT,
    ).select_related("lead", "campaign")
    if campaign:
        base = base.filter(campaign_id=campaign)
    if q:
        base = base.filter(
            Q(lead__email__icontains=q)
            | Q(lead__profile_url__icontains=q)
            | Q(lead__profile_text__icontains=q)
            | Q(reason__icontains=q)
            | Q(campaign__name__icontains=q)
        )

    from openoutreach.crm.models import RequalificationLog

    total = base.count()
    rows = list(base.order_by("-creation_date")[(page - 1) * PAGE_SIZE:page * PAGE_SIZE])

    # The override audit trail: every flip back to QUALIFIED, newest first —
    # who, when, which lead, and the original rejection reason. Paginated
    # separately from the rejection table (``logpage``).
    try:
        log_page = max(1, int(request.GET.get("logpage", 1)))
    except (TypeError, ValueError):
        log_page = 1
    log_qs = RequalificationLog.objects.select_related("deal", "user")
    log_total = log_qs.count()
    logs = list(log_qs[(log_page - 1) * PAGE_SIZE:log_page * PAGE_SIZE])

    since_midnight = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today = Deal.objects.filter(
        state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
        creation_date__gte=since_midnight,
    ).count()
    week_ago = since_midnight - timedelta(days=7)
    this_week = Deal.objects.filter(
        state=DealState.FAILED, outcome=Outcome.WRONG_FIT,
        creation_date__gte=week_ago,
    ).count()

    querystring = request.GET.copy()
    querystring.pop("page", None)
    log_querystring = request.GET.copy()
    log_querystring.pop("page", None)
    log_querystring.pop("logpage", None)
    return render(request, "dashboard/reviews.html", {
        "rows": rows,
        "campaigns": campaigns,
        "campaign": campaign,
        "q": q,
        "page": page,
        "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        "total": total,
        "today": today,
        "this_week": this_week,
        "logs": logs,
        "log_total": log_total,
        "log_page": log_page,
        "log_pages": max(1, (log_total + PAGE_SIZE - 1) // PAGE_SIZE),
        "querystring": querystring.urlencode(),
        "log_querystring": log_querystring.urlencode(),
    })


def research(request):
    """The researcher's mind: the discovery frontier, its vocabulary, verdicts.

    The discovery walk is a greedy descent over keyword sets — each ``QueryNode``
    is one query the researcher ran (or is about to run), valued by counting and
    sampled by Thompson. ``reason`` on a deal is the LLM qualifier's own words on
    why a profile fit or did not fit the campaign target.
    """
    from openoutreach.core.models import Keyword, QueryNode
    from openoutreach.discovery import describe_node

    campaigns = list(Campaign.objects.order_by("pk"))
    campaign = request.GET.get("campaign", "")
    cams = campaigns if not campaign else [c for c in campaigns if str(c.pk) == campaign]

    nodes = (
        QueryNode.objects.filter(campaign__in=cams)
        .select_related("campaign")
        .prefetch_related("keywords")
        .order_by("-updated_at")[:80]
    )
    node_rows = [{
        "node": n,
        "query": describe_node(n.pairs),
        "campaign": n.campaign.name,
        "state": n.state,
        "offset": n.next_offset,
        "leads_found": n.leads_found,
        "updated": n.updated_at,
    } for n in nodes]

    state_counts = dict(
        QueryNode.objects.filter(campaign__in=cams)
        .values_list("state").annotate(n=Count("state"))
    )
    vocab = (
        Keyword.objects.annotate(n=Count("nodes"))
        .filter(n__gt=0)
        .order_by("-n", "field", "token")[:40]
    )

    verdicts = (
        Deal.objects.filter(campaign__in=cams, state__in=[
            DealState.QUALIFIED, DealState.FAILED,
        ])
        .select_related("lead", "campaign")
        .order_by("-update_date")[:30]
    )

    return render(request, "dashboard/research.html", {
        "campaigns": campaigns,
        "campaign": campaign,
        "node_rows": node_rows,
        "state_counts": state_counts,
        "vocab": vocab,
        "verdicts": verdicts,
        "gp_threshold": GP_THRESHOLD,
        "pool": {
            "qualified": Deal.objects.filter(
                campaign__in=cams, state=DealState.QUALIFIED).count(),
            "rejected": Deal.objects.filter(
                campaign__in=cams, state=DealState.FAILED).count(),
            "emailed": Deal.objects.filter(
                campaign__in=cams, state=DealState.EMAILED).count(),
            "nodes": QueryNode.objects.filter(campaign__in=cams).count(),
            "keywords": Keyword.objects.count(),
        },
    })


@staff_member_required
def lookups(request):
    """Email finding — the one paid step. Who is waiting to buy an address,
    whose lookup is in flight, and who is ready to be emailed."""
    cfg = SiteConfig.load()

    def _deals(state):
        return (
            Deal.objects.filter(state=state, lead__disqualified=False)
            .select_related("lead", "campaign")
            .order_by("creation_date")
        )

    inflight = _deals(DealState.FINDING_EMAIL)
    waiting = _deals(DealState.READY_TO_FIND_EMAIL)
    ready = _deals(DealState.READY_TO_EMAIL)
    misses = Deal.objects.filter(state=DealState.NO_EMAIL_BETTERCONTACT).count()

    return render(request, "dashboard/lookups.html", {
        "inflight": inflight,
        "waiting": waiting,
        "ready": ready,
        "misses": misses,
        "bettercontact": bool(cfg.bettercontact_api_key),
        "firecrawl": bool(cfg.firecrawl_api_key),
        "now": timezone.now(),
        "gp_threshold": GP_THRESHOLD,
    })


@staff_member_required
def sends(request):
    """Sender + warm-up: the box's measured capacity, its pacing, its history.

    The daily cap is *measured* (``emails/warmth.py`` reads the box's own Sent
    folder), not configured — every row here is the receiver's answer to what the
    box has actually sustained. Verdicts are the only record of what the far end
    said about a send that failed.
    """
    from openoutreach.chat.models import ChatMessage

    q = request.GET.get("q", "").strip()
    campaign = request.GET.get("campaign", "")
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    campaigns = list(Campaign.objects.order_by("pk"))

    boxes = []
    for box in Mailbox.objects.order_by("pk"):
        boxes.append({
            "box": box,
            "sent_today": box.sent_today(),
            "headroom": box.headroom_today(),
            "paused": box.paused_today(),
            "manual_pause": box.send_paused,
        })

    history = (
        ChatMessage.objects.filter(is_outgoing=True)
        .select_related("deal", "deal__lead", "deal__campaign", "deal__mailbox")
        .order_by("-creation_date")
    )
    if campaign:
        history = history.filter(deal__campaign_id=campaign)
    if q:
        history = history.filter(
            Q(content__icontains=q)
            | Q(deal__lead__email__icontains=q)
            | Q(deal__lead__profile_url__icontains=q)
            | Q(deal__email_subject__icontains=q)
            | Q(deal__campaign__name__icontains=q)
        )
    total = history.count()
    history = list(history[(page - 1) * PAGE_SIZE:page * PAGE_SIZE])
    verdicts = SendVerdict.objects.select_related("mailbox").order_by("-created_at")[:60]
    sends_per_day = _sends_per_day(21)

    querystring = request.GET.copy()
    querystring.pop("page", None)
    return render(request, "dashboard/sends.html", {
        "boxes": boxes,
        "history": history,
        "verdicts": verdicts,
        "campaigns": campaigns,
        "campaign": campaign,
        "q": q,
        "page": page,
        "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        "total": total,
        "querystring": querystring.urlencode(),
        "spark_values": [row["n"] for row in sends_per_day],
        "spark_line": _spark([row["n"] for row in sends_per_day], w=260, h=48),
        "spark_area": _spark_area([row["n"] for row in sends_per_day], w=260, h=48),
        "spark_labels": [row["label"] for row in sends_per_day],
    })


# ── JSON endpoints ────────────────────────────────────────────


@staff_member_required
def sends_csv(request):
    """CSV export of the send log — same filters as the Sends page, no limit.

    Streams so the export never needs the whole table in memory; a UTF-8 BOM is
    prepended so spreadsheet apps decode the non-ASCII (French) bodies correctly.
    """
    import csv

    from django.http import StreamingHttpResponse

    from openoutreach.chat.models import ChatMessage

    q = request.GET.get("q", "").strip()
    campaign = request.GET.get("campaign", "")
    rows = (
        ChatMessage.objects.filter(is_outgoing=True)
        .select_related("deal", "deal__lead", "deal__campaign", "deal__mailbox")
        .order_by("creation_date")
    )
    if campaign:
        rows = rows.filter(deal__campaign_id=campaign)
    if q:
        rows = rows.filter(
            Q(content__icontains=q)
            | Q(deal__lead__email__icontains=q)
            | Q(deal__lead__profile_url__icontains=q)
            | Q(deal__email_subject__icontains=q)
            | Q(deal__campaign__name__icontains=q)
        )

    class _Echo:
        """A write() that returns the value — lets csv.writer stream."""

        def write(self, value):
            return value

    writer = csv.writer(_Echo())
    iterator = rows.iterator(chunk_size=500)

    def generate():
        yield "\ufeff"  # UTF-8 BOM for spreadsheet apps
        yield writer.writerow([
            "sent_at", "to", "lead_url", "subject", "campaign", "mailbox", "body",
        ])
        for msg in iterator:
            yield writer.writerow([
                msg.creation_date.strftime("%Y-%m-%d %H:%M"),
                msg.deal.lead.email or "",
                msg.deal.lead.profile_url or "",
                msg.deal.email_subject or "",
                msg.deal.campaign.name,
                msg.deal.mailbox.from_address if msg.deal.mailbox_id else "",
                msg.content or "",
            ])

    filename = f"send-log-{timezone.now():%Y%m%d}.csv"
    response = StreamingHttpResponse(generate(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@staff_member_required
def api_status(request):
    """The live slice the console polls — daemon, headline counts, boxes."""
    p = _overview_payload()
    # The API feeds the full log page too, so carry a real tail, not the
    # overview's short preview.
    p["log_tail"] = _daemon_log_tail(max_chars=16000)
    return JsonResponse({
        "daemon": {
            "running": p["daemon_running"],
            "status": p["daemon_status"],
            "log_tail": p["log_tail"],
        },
        "counts": {
            "total_leads": p["total_leads"],
            "total_deals": p["total_deals"],
            "sent_messages": p["sent_messages"],
            "unanswered": p["unanswered"],
        },
        "funnel": [
            {"state": row["state"], "label": row["label"], "count": row["count"]}
            for row in p["funnel"]
        ],
        "boxes": [_box_dict(b) for b in p["boxes"]],
        "updated_at": timezone.now().isoformat(),
    })


@staff_member_required
def api_deal(request, pk):
    """A deal's conversation thread, for the expandable Deals rows."""
    from openoutreach.chat.models import ChatMessage
    from openoutreach.crm.models import GpScoreRecord

    deal = get_object_or_404(Deal.objects.select_related("lead", "campaign"), pk=pk)
    msgs = ChatMessage.objects.filter(deal=deal).order_by("creation_date")
    history = list(GpScoreRecord.objects.filter(deal=deal).order_by("created_at"))
    latest = history[-1] if history else None
    return JsonResponse({
        "deal": {
            "pk": deal.pk,
            "state": dict(DealState.choices).get(deal.state, deal.state),
            "lead": str(deal.lead),
            "lead_url": deal.lead.profile_url or "",
            "campaign": deal.campaign.name,
            "email": deal.lead.email or "",
            "subject": deal.email_subject or "",
            "outcome": deal.outcome or "",
            "gp_score": deal.gp_score,
            "gp_threshold": GP_THRESHOLD,
            # The latest pass's own P/mean/std — always one consistent fit, unlike
            # `gp_score` (the frozen promotion-time snapshot) which may predate the
            # model's most recent refit.
            "gp_pass_score": round(latest.score, 4) if latest else None,
            "gp_pass_date": latest.created_at.strftime("%b %d %H:%M") if latest else None,
            "gp_mean": round(latest.mean, 4) if latest and latest.mean is not None else None,
            "gp_std": round(latest.std, 4) if latest and latest.std is not None else None,
        },
        "messages": [
            {
                "outgoing": m.is_outgoing,
                "date": m.creation_date.strftime("%b %d %H:%M"),
                "content": m.content,
            }
            for m in msgs
        ],
        "gp_history": [
            {
                "score": round(r.score, 4),
                "mean": round(r.mean, 4) if r.mean is not None else None,
                "std": round(r.std, 4) if r.std is not None else None,
                "date": r.created_at.strftime("%b %d %H:%M"),
            }
            for r in history
        ],
    })


# ── Controls ──────────────────────────────────────────────────


@staff_member_required
@require_POST
def action(request, name):
    back = request.POST.get("next") or "/dashboard/"
    if name == "daemon-start":
        _daemon().start()
    elif name == "daemon-stop":
        _daemon().stop()
    elif name == "cycle":
        _run_cycles()
    elif name == "read-mail":
        for box in Mailbox.objects.all():
            from openoutreach.emails.inbox import read_mail
            read_mail(box)
    elif name == "mailbox-pause":
        box = get_object_or_404(Mailbox, pk=request.POST.get("mailbox"))
        box.send_paused = True
        box.save(update_fields=["send_paused"])
    elif name == "mailbox-resume":
        box = get_object_or_404(Mailbox, pk=request.POST.get("mailbox"))
        box.send_paused = False
        box.save(update_fields=["send_paused"])
    elif name == "lead-suppress":
        lead = get_object_or_404(Lead, pk=request.POST.get("lead"))
        lead.disqualified = True
        lead.save(update_fields=["disqualified"])
    elif name == "lead-unsuppress":
        lead = get_object_or_404(Lead, pk=request.POST.get("lead"))
        lead.disqualified = False
        lead.save(update_fields=["disqualified"])
    elif name == "deal-requalify":
        from openoutreach.crm.models import Outcome

        deal = get_object_or_404(Deal, pk=request.POST.get("deal"))
        if deal.state != DealState.FAILED or deal.outcome != Outcome.WRONG_FIT:
            return HttpResponseBadRequest(
                "only rejected (wrong_fit) deals can be requalified")
        _requalify_deal(deal, request.user)
        messages.success(request, f"Requalified {deal.lead} — back in the qualified pool.")
    elif name == "deals-requalify":
        # Bulk Reviews override — flip every selected FAILED wrong_fit Deal back
        # to QUALIFIED in one click (checkbox selection on the Reviews page).
        from openoutreach.crm.models import Outcome

        pks = request.POST.getlist("deals")
        if not pks:
            return HttpResponseBadRequest("no deals selected")
        deals = list(Deal.objects.filter(pk__in=pks).select_related("lead"))
        if len(deals) != len(set(pks)):
            return HttpResponseBadRequest("one or more selected deals no longer exist")
        for deal in deals:
            if deal.state != DealState.FAILED or deal.outcome != Outcome.WRONG_FIT:
                return HttpResponseBadRequest(
                    "only rejected (wrong_fit) deals can be requalified")
        for deal in deals:
            _requalify_deal(deal, request.user)
        messages.success(
            request,
            f"Requalified {len(deals)} selected deal(s) — back in the qualified pool.",
        )
    else:
        return HttpResponseBadRequest(f"unknown action: {name}")
    return redirect(back)


def _requalify_deal(deal, user):
    """Override an LLM rejection — one-liner over the shared choke point in
    ``core/db/deals.py`` so the RequalificationLog is written exactly once per
    override, whichever action performed it."""
    from openoutreach.core.db.deals import requalify_deal

    requalify_deal(deal, user=user, source="reviews")


@lead_required
@require_POST
def save_config(request):
    back = request.POST.get("next") or "/dashboard/config/"
    section = request.POST.get("section")

    if section == "siteconfig":
        cfg = SiteConfig.load()
        cfg.ai_model = request.POST.get("ai_model", "").strip()
        cfg.llm_api_key = request.POST.get("llm_api_key", "").strip()
        cfg.llm_api_base = request.POST.get("llm_api_base", "").strip()
        cfg.bettercontact_api_key = request.POST.get("bettercontact_api_key", "").strip()
        cfg.firecrawl_api_key = request.POST.get("firecrawl_api_key", "").strip()
        cfg.country_code = request.POST.get("country_code", "").strip()[:2].lower()
        cfg.send_on_weekends = request.POST.get("send_on_weekends") == "1"
        cfg.keep_pipeline_stocked = request.POST.get("keep_pipeline_stocked") == "1"
        cfg.save()
        messages.success(request, "Provider settings saved.")
    elif section == "campaign":
        campaign = get_object_or_404(Campaign, pk=request.POST.get("campaign"))
        campaign.product_docs = request.POST.get("product_docs", "")
        campaign.campaign_target = request.POST.get("campaign_target", "")
        campaign.booking_link = request.POST.get("booking_link", "").strip()
        try:
            campaign.headcount_min = max(1, int(request.POST.get("headcount_min", 1)))
            campaign.headcount_max = max(1, int(request.POST.get("headcount_max", 10000)))
        except (TypeError, ValueError):
            pass
        campaign.country_code = request.POST.get("country_code", "").strip()[:2].lower()
        campaign.save()
        messages.success(request, "Campaign saved.")
    elif section == "mailbox":
        box = get_object_or_404(Mailbox, pk=request.POST.get("mailbox"))
        box.signature = request.POST.get("signature", "")
        box.imap_username = request.POST.get("imap_username", "").strip()
        box.imap_password = request.POST.get("imap_password", "").strip()
        try:
            box.daily_limit = max(0, int(request.POST.get("daily_limit", box.daily_limit)))
        except (TypeError, ValueError):
            pass
        box.save()
        messages.success(request, "Mailbox saved.")
    else:
        return HttpResponseBadRequest(f"unknown section: {section}")
    return redirect(back)


# ── Team management (Lead only) ───────────────────────────────


@lead_required
@require_POST
def team_add(request):
    name = request.POST.get("name", "").strip()
    email = request.POST.get("email", "").strip().lower()
    if not name or not email:
        return HttpResponseBadRequest("name and email are required")
    if User.objects.filter(email=email).exists():
        messages.error(request, f"A user with {email} already exists.")
        return redirect("dashboard-team")

    handle = email.split("@")[0].lower().replace(".", "_").replace("+", "_")
    username = handle
    n = 1
    while User.objects.filter(username=username).exists():
        n += 1
        username = f"{handle}{n}"

    password = secrets.token_urlsafe(9)
    user = User.objects.create_user(
        username=username,
        email=email,
        first_name=name,
        password=password,
        is_staff=True,
        is_active=True,
    )
    messages.success(
        request,
        f"Added <b>{name}</b> as an Operator. Temporary password: "
        f"<code>{password}</code> — share it privately; the dashboard does not "
        f"store it in the clear.",
    )
    return redirect("dashboard-team")


@lead_required
@require_POST
def team_toggle_active(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, "You cannot deactivate your own account.")
        return redirect("dashboard-team")
    user.is_active = not user.is_active
    user.save(update_fields=["is_active"])
    messages.success(
        request, f"{user.get_full_name() or user.username} is now "
        f"{'active' if user.is_active else 'deactivated'}."
    )
    return redirect("dashboard-team")


@lead_required
@require_POST
def team_role(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, "You cannot change your own role.")
        return redirect("dashboard-team")
    role = request.POST.get("role", "")
    if role not in ("lead", "operator"):
        return HttpResponseBadRequest("unknown role")
    if role == "operator" and user.is_superuser:
        remaining_leads = User.objects.filter(is_superuser=True).exclude(pk=user.pk).count()
        if remaining_leads == 0:
            messages.error(request, "The studio needs at least one Lead — promote someone first.")
            return redirect("dashboard-team")
    user.is_superuser = role == "lead"
    user.save(update_fields=["is_superuser"])
    messages.success(
        request,
        f"{user.get_full_name() or user.username} is now "
        f"{'the studio Lead' if role == 'lead' else 'an Operator'}.",
    )
    return redirect("dashboard-team")


@lead_required
@require_POST
def team_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    password = secrets.token_urlsafe(9)
    user.set_password(password)
    user.save(update_fields=["password"])
    messages.success(
        request,
        f"New temporary password for <b>{user.get_full_name() or user.username}</b>: "
        f"<code>{password}</code>",
    )
    return redirect("dashboard-team")


# ── Helpers ───────────────────────────────────────────────────


def _spark_area(values: list[int], w: int = 96, h: int = 30, pad: int = 3) -> str:
    """An SVG filled path under the sparkline (line + close to the bottom)."""
    if not values:
        return ""
    mx = max(values) or 1
    step = (w - 2 * pad) / max(len(values) - 1, 1)
    pts = [f"{pad},{h - pad}"]
    for i, v in enumerate(values):
        x = pad + i * step
        y = h - pad - (v / mx) * (h - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    pts.append(f"{w - pad},{h - pad}")
    return " ".join(pts)


def _daemon():
    from openoutreach.dashboard import daemon
    return daemon


def _daemon_status() -> str:
    try:
        return _daemon().status()
    except Exception as exc:  # never let the panel die on a process check
        logger.warning("daemon status check failed: %s", exc)
        return "unknown"


def _daemon_log_tail(max_chars: int = 4000) -> str:
    try:
        return _daemon().tail(max_chars=max_chars)
    except Exception as exc:
        logger.warning("daemon log tail failed: %s", exc)
        return f"(could not read log: {exc})"


def _run_cycles() -> int:
    """One action per operator campaign, like the daemon's rotation — stop at
    the first that did something, so the nudge never out-runs the daemon."""
    from openoutreach.core.cycle import run_one_action
    from openoutreach.core.operator import campaigns

    acted = 0
    for campaign in campaigns():
        if run_one_action(campaign):
            acted += 1
            break
    return acted
