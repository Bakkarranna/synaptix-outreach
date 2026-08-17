# openoutreach/core/pipeline/scrape_acquisition.py
"""Scraper-backed acquisition — one directory page per call, harvested into Leads.

Opt-in per campaign: when ``Campaign.scraper_config`` is set (a source spec in
the shape the standalone ``scrape_leads.py`` tool documents), the campaign's
top-up stops running the Lead Finder discovery walk and instead pages one
directory page per cycle, harvesting each card through the same ``create_lead``
entry point discovery and CSV import use. Deduped on ``profile_url``, embedded
and qualified exactly like discovered leads.

Deliberately lighter than the standalone tool, because it runs *inside the
daemon cycle* (one action per ~5s, and a slow action blocks the loop):

- **Card-level fields only.** No per-card detail-page fetch. Emails visible on
  the listing page are stamped (which skips the paid lookup); the rest are
  resolved downstream by the funnel's own free site-finder before any paid
  submit, so ``company_domain`` is always set from the website link.
- **Politeness defaults.** ``delay_s`` between pages (a scrape is a public
  directory, not a licensed API), one page per cycle, and acquisition stops for
  good when the directory ends or ``max_pages`` is reached.

State rides on the campaign: ``Campaign.scrape_state`` = ``{"next_url",
"pages", "exhausted"}`` survives restarts, so a reboot resumes mid-directory.
Natural end (no next link, empty page) or ``max_pages`` sets ``exhausted``;
clearing ``scrape_state`` in the Django Admin re-scrapes from the start —
idempotent, since existing ``profile_url`` rows are skipped by ``create_lead``.
Editing ``scraper_config`` and clearing ``scrape_state`` together restarts a
sweep under new selectors.

Requires Scrapling installed (guarded import, same convention as
``emails/finders.py``); without it the step logs and the campaign idles.
``phone`` and ``website`` are stamped on the lead (real columns, folded into
``profile_text`` via ``discovery.TEXT_FIELDS``) exactly like ``email``.

The extraction language (``fields`` specs, ``card_selector``, ``next_selector``
…) is identical to the standalone ``scrape_leads.py`` — keep the two copies of
``_extract_field``/``_row_from_card`` in sync.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# ── patterns ───────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?[\d(][\d\s().\-/]{6,}\d")
_WS_RE = re.compile(r"\s+")

# The same junk semantics emails/finders.py enforces (the canonical copy) —
# vendored here because core must not import the channel app. ``plausible``
# mirrors finders.plausible: an address is only accepted when it is well-formed,
# not junk, and — when the company domain is known — on that exact domain, since
# a stamped email is trusted by the send path.
_JUNK_LOCALS = frozenset({
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "sentry", "wixpress", "wordpress", "example", "test", "user", "username",
    "yourname", "email", "domain", "website", "changeme", "invalid",
})
_JUNK_DOMAINS = frozenset({
    "sentry.io", "wixpress.com", "wordpress.com", "godaddy.com", "example.com",
    "yourdomain.com", "email.com", "website.com", "domain.com", "wix.com",
    "mailinator.com", "yopmail.com", "guerrillamail.com",
})
_GENERIC_LOCALS = frozenset({
    "info", "contact", "hello", "support", "sales", "admin", "office",
    "enquiries", "inquiries", "team", "mail", "email", "careers", "jobs",
})


def _collapse(text: str) -> str:
    return _WS_RE.sub(" ", text or "").strip()


def _plausible_email(email: str, company_domain: str = "") -> bool:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.fullmatch(email):
        return False
    local, _, domain = email.partition("@")
    if local in _JUNK_LOCALS or domain in _JUNK_DOMAINS:
        return False
    if any(token in local for token in ("noreply", "donotreply", "sentry")):
        return False
    if company_domain:
        return domain == company_domain.lower()
    return True


def _extract_emails(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _EMAIL_RE.findall(text or ""):
        email = match.lower()
        if email not in seen and _plausible_email(email):
            seen.add(email)
            out.append(email)
    return out


def _extract_phones(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for cand in _PHONE_RE.findall(text or ""):
        digits = re.sub(r"\D", "", cand)
        if 7 <= len(digits) <= 15:
            norm = ("+" if cand.strip().startswith("+") else "") + digits
            if norm not in seen:
                seen.add(norm)
                out.append(norm)
    return out


def _pick_email(emails: list[str], company_domain: str = "") -> str:
    """The address worth stamping: company-domain > non-generic > first."""
    if not emails:
        return ""

    def score(email: str) -> int:
        local, _, domain = email.partition("@")
        s = 0
        if company_domain and domain == company_domain:
            s += 3
        if local not in _GENERIC_LOCALS:
            s += 1
        return s

    return max(emails, key=score)


def _split_name(name: str) -> tuple[str, str]:
    parts = _collapse(name).split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def _person_slug(row: dict) -> str:
    """A stable per-person key fragment for a synthetic profile URL."""
    parts = [row.get("contact_first_name", ""), row.get("contact_last_name", "")]
    slug = "-".join(p for p in parts if p).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or "lead"


# ── Scrapling helpers ─────────────────────────────────────────────

def _text_of(el) -> str:
    """The element's full inner text, whatever the version's API surface."""
    try:
        parts = el.css("::text").getall()
        return " ".join(str(p).strip() for p in parts if str(p).strip())
    except Exception:  # noqa: BLE001 — parser version differences
        pass
    try:
        return _collapse(el.get_all_text())
    except Exception:  # noqa: BLE001
        try:
            return _collapse(str(el.text))
        except Exception:  # noqa: BLE001
            return ""


def _extract_field(el, spec: dict) -> str:
    """One extraction spec against one element (card or document).

    Text is read as the matched element's *full* inner text (all descendant
    text nodes — ``h2 > a`` text included). A user-supplied ``css`` may already
    carry a ``::text``/``::attr(...)`` pseudo-element; if so it is used as-is,
    otherwise the appropriate one is appended. Same language as the standalone
    ``scrape_leads.py``.
    """
    if "const" in spec:
        return str(spec["const"])

    if "regex" in spec and "css" not in spec:
        match = re.search(spec["regex"], _text_of(el))
        return match.group(spec.get("group", 1)).strip() if match else ""

    css = spec.get("css", "")
    vals: list[str] = []
    if css:
        if spec.get("attr"):
            target = css if "::" in css else f"{css}::attr({spec['attr']})"
            vals = [str(v).strip() for v in el.css(target).getall()]
        elif "::" in css:
            vals = [str(v).strip() for v in el.css(css).getall()]
        else:
            elements = el.css(css)
            vals = [_collapse(_text_of(e)) for e in elements]
    vals = [v for v in vals if v]
    if not vals:
        return ""

    if "regex" in spec:
        match = re.search(spec["regex"], " ".join(vals))
        return match.group(spec.get("group", 1)).strip() if match else ""
    if spec.get("all"):
        return " | ".join(vals)
    return vals[0]


def _first_href(el, css: str, base_url: str) -> str:
    """Absolute URL of the first match's href. ``css`` may already carry
    ``::attr(href)``; otherwise it is appended."""
    if not css:
        return ""
    if "::attr(" not in css:
        css = f"{css}::attr(href)"
    for href in el.css(css).getall():
        href = str(href).strip()
        if href:
            return urljoin(base_url, href)
    return ""


def _first_http_link(el, base_url: str, exclude_host: str = "") -> str:
    """First http(s) link that isn't mailto/tel/js and (when ``exclude_host``)
    isn't the page's own host — a directory page has a lot of self-links."""
    for href in el.css("a::attr(href)").getall():
        href = str(href).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href)
        if url.startswith(("http://", "https://")):
            host = (urlparse(url).netloc or "").lower()
            if exclude_host and host == exclude_host:
                continue
            return url
    return ""


def _domain_of(url: str) -> str:
    if not url:
        return ""
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def _netloc(url: str) -> str:
    return (urlparse(url).netloc or "").lower()


def _fetch(url: str, mode: str, timeout_s: float):
    """One Scrapling fetch; ``None`` on failure (guarded import, like finders)."""
    try:
        if mode == "stealth":
            try:
                from scrapling.fetchers import StealthyFetcher
            except ImportError:
                logger.warning(
                    "scraper mode 'stealth' needs scrapling[fetchers] and "
                    "`scrapling install` (browsers) — skipping page %s", url)
                return None
            logging.getLogger("scrapling").setLevel(logging.WARNING)
            return StealthyFetcher.fetch(
                url, headless=True, network_idle=True,
                timeout=int(timeout_s * 1000), disable_resources=True)
        try:
            from scrapling.fetchers import Fetcher
        except ImportError:
            logger.warning(
                "scraper acquisition needs scrapling installed "
                "(`pip install \"scrapling[fetchers]\"`) — skipping page %s", url)
            return None
        logging.getLogger("scrapling").setLevel(logging.WARNING)
        return Fetcher.get(url, timeout=timeout_s, stealthy_headers=True,
                           impersonate="chrome")
    except Exception as exc:  # noqa: BLE001 — network / anti-bot / parse
        logger.warning("scrape fetch failed for %s (%s: %s)",
                       url, type(exc).__name__, exc)
        return None


# ── card → row ────────────────────────────────────────────────────

def _row_from_card(card, page_url: str, cfg: dict) -> dict | None:
    """One listing card → one portable lead row (the ``create_lead`` shape).

    ``None`` when nothing can key the lead. The profile key prefers a real
    LinkedIn URL (``profile_url_selector``); otherwise a synthetic
    ``<website>#scraped-<name-slug>`` URL keeps per-person dedup correct (the
    website alone would collapse everyone at one company onto one row). Note
    the paid finder's lookup assumes a LinkedIn-shaped key, so leads whose
    email is not stamped here rely on the funnel's free site-finder first —
    which is exactly why ``company_domain`` is always set.
    """
    row: dict = {}
    for col, spec in (cfg.get("fields") or {}).items():
        value = _extract_field(card, spec)
        if value:
            row[col] = value

    text = _text_of(card)
    emails = _extract_emails(text)
    for mailto in card.css("a[href^='mailto:']::attr(href)").getall():
        email = str(mailto).split(":", 1)[1].split("?", 1)[0].strip().lower()
        if email and _plausible_email(email) and email not in emails:
            emails.append(email)
    phones = _extract_phones(text)

    if "contact_name" in row:
        first, last = _split_name(row.pop("contact_name"))
        row.setdefault("contact_first_name", first)
        row.setdefault("contact_last_name", last)

    website = ""
    website_selector = cfg.get("website_selector")
    if website_selector:
        website = _first_href(card, website_selector, page_url)
    if not website:
        website = _first_http_link(card, page_url, exclude_host=_netloc(page_url))
    if website:
        row["company_domain"] = _domain_of(website)
        row["website"] = website

    profile_url = ""
    profile_selector = cfg.get("profile_url_selector")
    if profile_selector:
        profile_url = _first_href(card, profile_selector, page_url)
    if not profile_url and website:
        profile_url = f"{website.rstrip('/')}/#scraped-{_person_slug(row)}"
    if not profile_url:
        return None
    # ``create_lead`` reads the provider-style key — the CSV importer maps
    # ``profile_url`` to it before calling, the scraper writes it directly.
    row["contact_linkedin_profile_url"] = profile_url

    if "contact_headline" not in row and text:
        row["contact_headline"] = _collapse(text)[:200]
    if emails:
        row["email"] = _pick_email(emails, row.get("company_domain", ""))
    if phones:
        row["phone"] = " | ".join(phones)
    return row


# ── the acquisition move ──────────────────────────────────────────

def scrape_one_page(campaign) -> int:
    """Fetch the campaign's next directory page and harvest new Leads.

    Returns the number of *new* leads created (0 when the campaign is
    exhausted, a fetch failed, or the page yielded nothing new). Advances the
    pagination cursor in ``campaign.scrape_state`` — the only retry mechanism
    is the caller coming back next cycle, since a fetch failure leaves the
    cursor untouched.
    """
    cfg = campaign.scraper_config or {}
    state = dict(campaign.scrape_state or {})
    if state.get("exhausted"):
        return 0

    start_url = cfg.get("start_url", "")
    card_selector = cfg.get("card_selector", "")
    if not start_url or not card_selector:
        logger.error(
            "[%s] scraper_config needs start_url and card_selector — "
            "disabling scraper acquisition", campaign)
        state["exhausted"] = True
        campaign.scrape_state = state
        campaign.save(update_fields=["scrape_state"])
        return 0

    page_url = state.get("next_url") or start_url
    fetch = cfg.get("fetch", "http")
    timeout_s = float(cfg.get("timeout_s", 15))
    max_cards = int(cfg.get("max_cards_per_page", 100))

    resp = _fetch(page_url, fetch, timeout_s)
    if resp is None or resp.status >= 400:
        logger.warning("[%s] scrape page failed (%s) — retrying next cycle",
                       campaign, getattr(resp, "status", "no response"))
        return 0

    cards = resp.css(card_selector)
    created = 0
    if not cards:
        if not state.get("pages"):
            # A selector matching nothing on the very first page is a config
            # error, not the end of a directory — stop and say so.
            logger.error("[%s] no cards matching %r on %s — check "
                         "scraper_config.card_selector; disabling", campaign,
                         card_selector, page_url)
        else:
            logger.info("[%s] no more cards on %s — directory drained", campaign, page_url)
        state["exhausted"] = True
    else:
        from openoutreach.core.db.leads import create_lead
        from openoutreach.crm.models import Lead

        country = campaign.country_code or ""
        for card in cards[:max_cards]:
            row = _row_from_card(card, page_url, cfg)
            if not row:
                continue
            if create_lead(row, country_code=country):
                created += 1
                # ``create_lead`` never writes contact columns (discovery rows must
                # stay null); stamp them here, exactly like the CSV importer does.
                updates = {}
                for column in ("email", "phone", "website"):
                    if row.get(column):
                        updates[column] = row[column]
                if updates:
                    Lead.objects.filter(
                        profile_url=row["contact_linkedin_profile_url"],
                    ).update(**updates)

    state["pages"] = state.get("pages", 0) + 1
    next_selector = cfg.get("next_selector")
    next_url = _first_href(resp, next_selector, page_url) if next_selector else ""
    max_pages = int(cfg.get("max_pages", 0))
    if next_url and (not max_pages or state["pages"] < max_pages):
        state["next_url"] = next_url
    else:
        state["exhausted"] = True
        state.pop("next_url", None)

    campaign.scrape_state = state
    campaign.save(update_fields=["scrape_state"])
    if state.get("exhausted"):
        logger.info("[%s] scraper acquisition finished after %d page(s), "
                    "%d new lead(s)", campaign, state["pages"], created)
    elif cfg.get("delay_s"):
        time.sleep(float(cfg["delay_s"]))
    return created
