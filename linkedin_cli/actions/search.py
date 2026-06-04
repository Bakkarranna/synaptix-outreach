import json
import logging
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode

from linkedin_cli.browser.nav import goto_page, extract_in_urls

# LinkedIn connection-degree filter codes for People search (`network` facet).
NETWORK_CODES = {"first": "F", "second": "S", "third": "O"}

logger = logging.getLogger(__name__)

SELECTORS = {
    "search_bar": "//input[contains(@placeholder, 'Search')]",
    "profile_links": 'a[href*="/in/"]',
}


def _go_to_profile(session: "LinkedInSession", url: str, public_identifier: str):
    if f"/in/{public_identifier}" in session.page.url:
        return
    logger.debug("Direct navigation → %s", public_identifier)
    try:
        goto_page(
            session,
            action=lambda: session.page.goto(url, wait_until="domcontentloaded"),
            expected_url_pattern=f"/in/{public_identifier}",
            error_message="Failed to navigate to the target profile"
        )
    except RuntimeError:
        # Redirect to a different /in/ slug is tolerated; reconciling the
        # lead's stored slug is the caller's job (this layer holds no DB).
        if not _detect_profile_redirect(session, public_identifier):
            raise


def _detect_profile_redirect(session, old_public_id: str) -> str | None:
    """Return the new public_id if LinkedIn redirected to a different /in/ slug."""
    from urllib.parse import unquote
    from linkedin_cli.url_utils import url_to_public_id

    new_id = url_to_public_id(unquote(session.page.url))
    if new_id and new_id != old_public_id:
        logger.info("Profile redirect: %s → %s", old_public_id, new_id)
        return new_id
    return None


def visit_profile(session: "LinkedInSession", profile: Dict[str, Any]):
    public_identifier = profile.get("public_identifier")

    # Ensure browser is alive before doing anything
    session.ensure_browser()

    already_there = f"/in/{public_identifier}" in session.page.url

    if already_there:
        return

    url = profile.get("url")
    _go_to_profile(session, url, public_identifier)

    # Emit the /in/ profile URLs visible on the page; enrichment is caller-side.
    return extract_in_urls(session.page)


def _search_url(keyword: str, page: int = 1, network=None) -> str:
    """Build a People-search results URL, optionally filtered by connection degree.

    *network* is an optional list of degree codes — ``F`` (1st), ``S`` (2nd),
    ``O`` (3rd+) — passed to LinkedIn's ``network`` facet as a JSON array.
    """
    params = {"keywords": keyword, "origin": "FACETED_SEARCH"}
    if network:
        params["network"] = json.dumps(list(network))
    if page > 1:
        params["page"] = page
    return "https://www.linkedin.com/search/results/people/?" + urlencode(params)


def _initiate_search(session: "LinkedInSession", keyword: str):
    """Navigate directly to LinkedIn People search results for *keyword*."""
    goto_page(
        session,
        action=lambda: session.page.goto(_search_url(keyword)),
        expected_url_pattern="/search/results/people/",
        error_message="Failed to reach People search results",
    )


def _paginate_to_next_page(session: "LinkedInSession", page_num: int):
    page = session.page
    current = urlparse(page.url)
    params = parse_qs(current.query)
    params["page"] = [str(page_num)]
    new_url = current._replace(query=urlencode(params, doseq=True)).geturl()

    logger.debug("Scanning search page %s", page_num)
    goto_page(
        session,
        action=lambda: page.goto(new_url),
        expected_url_pattern="/search/results/",
        error_message="Pagination failed"
    )


def search_people(session: "LinkedInSession", keyword: str, page: int = 1, network=None) -> dict:
    """Search LinkedIn People; return the result page as a structured envelope.

    *network* optionally filters by connection degree (a list of `F`/`S`/`O`
    codes). Results carry only ``{public_identifier, url}`` — no `urn`; a
    follow-up `profile` scrape per url resolves the rest. Returns::

        {"query": ..., "page": ..., "network": [...]|None,
         "profiles": [{"public_identifier": ..., "url": ...}, ...]}
    """
    from linkedin_cli.url_utils import url_to_public_id

    session.ensure_browser()
    goto_page(
        session,
        action=lambda: session.page.goto(_search_url(keyword, page, network)),
        expected_url_pattern="/search/results/people/",
        error_message="Failed to reach People search results",
    )

    profiles, seen = [], set()
    for url in extract_in_urls(session.page):
        public_id = url_to_public_id(url)
        if public_id and public_id not in seen:
            seen.add(public_id)
            profiles.append({"public_identifier": public_id, "url": url})

    return {"query": keyword, "page": page,
            "network": list(network) if network else None, "profiles": profiles}


def _simulate_human_search(session: "LinkedInSession", profile: Dict[str, Any]) -> bool:
    full_name = profile.get("full_name")
    public_identifier = profile.get("public_identifier")

    # Reconstruct full_name if it's missing
    if not full_name:
        first = profile.get("first_name", "").strip()
        last = profile.get("last_name", "").strip()
        if first or last:
            full_name = f"{first} {last}".strip() if first and last else (first or last)
        else:
            logger.error(f"No name available for {public_identifier}")
            logger.debug(profile)
            return False

    if not public_identifier:
        logger.error(f"Missing public_identifier for '{full_name}'")
        raise ValueError("public_identifier is required")

    logger.info(f"Human search → '{full_name}' (target: {public_identifier})")

    _initiate_search(session, full_name)

    max_pages_to_scan = 1

    for current_page in range(1, max_pages_to_scan + 1):
        logger.info("Scanning search results page %s", current_page)

        target_locator = None
        for link in session.page.locator(SELECTORS["profile_links"]).all():
            href = link.get_attribute("href") or ""
            if f"/in/{public_identifier}" in href:
                target_locator = link
                break

        if target_locator:
            logger.info("Target found in results → clicking")
            return False

        if session.page.get_by_text("No results found", exact=False).count() > 0:
            logger.info("No results found → stopping search")
            break

        if current_page < max_pages_to_scan:
            _paginate_to_next_page(session, current_page + 1)
            session.wait()

    logger.info("Target %s not found → falling back to direct URL", public_identifier)
    return False
