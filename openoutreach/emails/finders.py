# openoutreach/emails/finders.py
"""Free email discovery from the lead's own company website.

The paid finder (BetterContact) is not the only source: a company that is live
publishes contact addresses on its own website, and for an SMB that address is
often the decision-maker's own inbox. This module is the free first draft of the
enrichment funnel — scraped *before* any paid submit, exactly like the hub cache:

    lead.email → hub cache → site finders (this module) → BetterContact submit

Three scrapers run cheapest-first, each one dropping out silently when it cannot
run (no API key, package not installed, site unreachable, no emails on the page):

  * ``site_emails`` — plain HTTP fetch of the site's common contact pages. Zero
    dependencies; works for the majority of small-business sites.
  * ``firecrawl_emails`` — Firecrawl scrape of the same pages (API key on
    ``SiteConfig.firecrawl_api_key``). Handles JS-rendered and bot-protected
    sites the plain fetch cannot read.
  * ``scrapling_emails`` — local Scrapling fetch (only if the package is
    installed). Plain HTTP first; the stealth browser
    (``StealthyFetcher`` — Cloudflare/Turnstile bypass, JS-rendered content)
    launches only when the plain fetch failed or carried no usable address,
    since a browser launch costs seconds per site. A last resort for the
    hardest sites; guarded import.

A found address is only accepted when ``plausible``: well-formed, not a known
junk address (sentry, wixpress, ``noreply`` …), and on the company's own domain
when one is known (else a domain that resolves). It is a real address the
company published, but it carries no provider verification status, so the
pipeline treats it as lower-confidence than a BetterContact ``deliverable`` hit
— still, a genuine alternative to a terminal miss.
"""
from __future__ import annotations

import logging
import re
import socket

import requests

logger = logging.getLogger(__name__)

# Cloudflare 403s a non-browser User-Agent (error 1010) — same as bettercontact.py.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Local parts that are never a decision-maker (or are placeholders), and domains
# that are never a real recipient. Kept deliberately short: over-filtering loses
# real addresses, and the company-domain rule below already rejects the worst.
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


def extract_emails(text: str) -> list[str]:
    """Every email address in ``text``, lowercased, deduped, in order found."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _EMAIL_RE.findall(text or ""):
        email = match.lower()
        if email not in seen:
            seen.add(email)
            out.append(email)
    return out


def plausible(email: str, company_domain: str = "") -> bool:
    """Is this address one we would actually mail?

    Well-formed, not a known junk address, and — when the company's domain is
    known — on that exact domain. Without a company domain, the address's own
    domain must at least resolve (a domain with no A record cannot receive mail).
    """
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
    return _domain_resolves(domain)


def _domain_resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, None)
    except OSError:
        return False
    return True


# ── scrapers ──────────────────────────────────────────────────────


def _pages_for(domain: str) -> list[str]:
    """The site paths most likely to carry a contact address, homepage first."""
    return [f"https://{domain}{path}" for path in (
        "", "/contact", "/contact-us", "/about", "/about-us", "/team",
    )]


def _fetch(url: str, timeout: float) -> str:
    """GET one page; returns body text. Raises on a non-HTML or failed fetch."""
    resp = requests.get(
        url, headers={"User-Agent": _BROWSER_UA}, timeout=timeout)
    resp.raise_for_status()
    content_type = resp.headers.get("content-type", "")
    if "text" not in content_type and "html" not in content_type:
        raise requests.RequestException(f"non-HTML content: {content_type}")
    return resp.text


def site_emails(domain: str, max_pages: int = 4, timeout: float = 5.0) -> list[str]:
    """Plain-HTTP scrape of the company site; plausible addresses only.

    Stops at the first page that yields anything — the homepage usually has a
    footer contact, and if not the contact page is next. A dead or slow site
    costs at most ``max_pages × timeout`` and is skipped silently.
    """
    domain = (domain or "").strip().lower().lstrip("www.")
    if not domain:
        return []
    for url in _pages_for(domain)[:max_pages]:
        try:
            text = _fetch(url, timeout)
        except (requests.RequestException, TimeoutError, ValueError):
            # try the plain-HTTP sibling once (http→https redirects are common
            # the other way; a bare-IP or misconfigured cert can need this)
            if url.startswith("https://"):
                try:
                    text = _fetch(url.replace("https://", "http://", 1), timeout)
                except (requests.RequestException, TimeoutError, ValueError):
                    continue
            else:
                continue
        found = [e for e in extract_emails(text) if plausible(e, domain)]
        if found:
            return found
    return []


def firecrawl_emails(domain: str, api_key: str, timeout: float = 20.0) -> list[str]:
    """Scrape the site through Firecrawl (JS + anti-bot); plausible addresses only.

    ``api_key`` blank ⇒ ``[]`` (the orchestrator treats it as not configured).
    """
    domain = (domain or "").strip().lower().lstrip("www.")
    if not domain or not api_key:
        return []
    found: list[str] = []
    for path in ("", "/contact"):
        try:
            resp = requests.post(
                "https://api.firecrawl.dev/v2/scrape",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"url": f"https://{domain}{path}",
                      "formats": ["markdown"], "onlyMainContent": False},
                timeout=timeout,
            )
            resp.raise_for_status()
            markdown = (resp.json().get("data") or {}).get("markdown") or ""
        except (requests.RequestException, TimeoutError, ValueError) as exc:
            logger.info("firecrawl scrape of %s failed: %s", domain, exc)
            continue
        for email in extract_emails(markdown):
            if plausible(email, domain) and email not in found:
                found.append(email)
        if found:
            return found
    return found


def _page_text(page) -> str:
    """Full inner text of a Scrapling response — the descendant text nodes.

    ``page.text`` is the root element's *direct* text, which for a whole
    document is usually empty; the emails live inside descendant nodes, so they
    are read with ``::text`` (with ``get_all_text`` as a cross-version fallback).
    """
    try:
        parts = page.css("::text").getall()
        return " ".join(str(p).strip() for p in parts if str(p).strip())
    except Exception:  # noqa: BLE001 — parser version differences
        try:
            return page.get_all_text()
        except Exception:  # noqa: BLE001
            return ""


def scrapling_emails(domain: str, timeout: float = 15.0) -> list[str]:
    """Local Scrapling fetch of the homepage; plausible addresses only.

    Two fetchers, cheap first: the plain HTTP ``Fetcher`` runs for every site,
    and the stealth browser (``StealthyFetcher`` — Cloudflare/Turnstile bypass,
    JS-rendered content) launches only when the plain fetch failed or carried
    no usable address — a browser launch costs seconds per site and must not
    run for a site the plain fetch already read. The stealth leg needs the
    browsers installed (``scrapling install``); without them it logs and the
    function returns what the plain fetch found.

    Scrapling is an optional dependency. Guarded import: not installed ⇒ ``[]``,
    and any failure inside is treated the same — best-effort last resort.
    """
    domain = (domain or "").strip().lower().lstrip("www.")
    if not domain:
        return []
    try:
        from scrapling import Fetcher  # type: ignore[import-not-found]
        from scrapling.fetchers import StealthyFetcher  # type: ignore[import-not-found]
    except ImportError:
        return []
    # Scrapling sets its own INFO-level handler at import time; keep the daemon
    # log lines readable.
    logging.getLogger("scrapling").setLevel(logging.WARNING)

    url = f"https://{domain}"
    attempts = (
        ("http", lambda: Fetcher.get(url, timeout=timeout)),
        ("stealth", lambda: StealthyFetcher.fetch(
            url, headless=True, network_idle=True,
            timeout=int(timeout * 1000), disable_resources=True)),
    )
    for name, fetch in attempts:
        try:
            page = fetch()
        except Exception as exc:  # noqa: BLE001 — best-effort provider
            logger.info("scrapling %s fetch of %s failed: %s", name, domain, exc)
            continue
        found = [e for e in extract_emails(_page_text(page)) if plausible(e, domain)]
        if found:
            return found
    return []


# ── orchestration ─────────────────────────────────────────────────


def _firecrawl_key() -> str:
    from openoutreach.core.models import SiteConfig
    return SiteConfig.load().firecrawl_api_key


def _best(candidates: list[str], first_name: str) -> str:
    """Prefer the address that looks like the person's own (first-name match).

    ``hello@`` / ``info@`` is a real, deliverable contact — but a ``name@``
    address is the decision-maker's inbox, which is the point of outreach.
    """
    if first_name:
        for email in candidates:
            local = email.split("@", 1)[0]
            if first_name in local or local.startswith(first_name[:3]):
                return email
    return candidates[0]


def find_emails(lead) -> str | None:
    """One free enrichment attempt: scrape the lead's company site.

    Cheapest source first; the first source with a plausible address wins.
    ``None`` when there is no company domain to scrape (nothing to fetch — the
    paid finder stays the only path for those leads), no scraper can run, or
    the site carries no usable address.
    """
    fields = lead.source_fields or {}
    domain = (fields.get("company_domain") or "").strip().lower().lstrip("www.")
    if not domain:
        return None
    first_name = (fields.get("contact_first_name") or "").strip().lower()

    def sources():
        yield site_emails(domain)
        yield firecrawl_emails(domain, _firecrawl_key())
        yield scrapling_emails(domain)

    # Lazy on purpose: the next scraper only runs when the previous one found
    # nothing — a Firecrawl call costs a credit-free request and ~seconds, so it
    # must not run when the plain-HTTP fetch already resolved the address.
    for candidates in sources():
        if candidates:
            return _best(candidates, first_name)
    return None
