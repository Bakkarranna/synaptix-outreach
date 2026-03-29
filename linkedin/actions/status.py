# linkedin/actions/status.py
import logging
from typing import Dict, Any

from linkedin.actions.connect import SELECTORS as CONNECT_SELECTORS
from linkedin.actions.search import visit_profile
from linkedin.enums import ProfileState
from linkedin.browser.nav import find_top_card

logger = logging.getLogger(__name__)

SELECTORS = {
    "pending_button": '[aria-label*="Pending"]',
    "invite_to_connect": CONNECT_SELECTORS["invite_to_connect"],
    "message_button": 'a[href*="/messaging/compose/"]:visible, button:has-text("Message"):visible',
    "more_button": CONNECT_SELECTORS["more_button"],
    "connect_option": CONNECT_SELECTORS["connect_option"],
}


def get_connection_status(
        session: "AccountSession",
        profile: Dict[str, Any],
) -> ProfileState:
    """
    Reliably detects connection status using UI inspection.
    Only trusts degree=1 as CONNECTED. Everything else is verified on the page.
    """
    # Ensure browser is ready (safe to call multiple times)
    session.ensure_browser()
    visit_profile(session, profile)
    session.wait()

    logger.debug("Checking connection status → %s", profile.get("public_identifier"))

    degree = profile.get("connection_degree", None)

    # Fast path: API says 1st degree → trust it
    if degree == 1:
        logger.debug("API reports 1st degree → instantly trusted as CONNECTED")
        return ProfileState.CONNECTED

    logger.debug("connection_degree=%s → falling back to UI inspection", degree or "None")

    top_card = find_top_card(session)

    has_pending = top_card.locator(SELECTORS["pending_button"]).count() > 0
    has_connect = top_card.locator(SELECTORS["invite_to_connect"]).count() > 0
    has_message = top_card.locator(SELECTORS["message_button"]).count() > 0

    if has_pending:
        logger.debug("Detected 'Pending' button → PENDING")
        return ProfileState.PENDING

    if has_connect:
        logger.debug("Found 'Connect' button → NOT_CONNECTED")
        return ProfileState.QUALIFIED

    # Connect might be hidden in the More menu (Open Profiles show Message without being connected)
    has_connect_in_more = _has_connect_in_more(session, top_card)
    if has_connect_in_more:
        logger.debug("Found 'Connect' in More menu → NOT_CONNECTED")
        return ProfileState.QUALIFIED

    if has_message:
        logger.debug("Detected 'Message' button (no Connect anywhere) → CONNECTED")
        return ProfileState.CONNECTED

    logger.debug("No clear indicators → defaulting to NOT_CONNECTED")
    return ProfileState.QUALIFIED


def _has_connect_in_more(session, top_card) -> bool:
    more = top_card.locator(SELECTORS["more_button"])
    if more.count() == 0:
        return False
    more.first.click()
    session.wait()
    found = top_card.locator(SELECTORS["connect_option"]).count() > 0
    if not found:
        session.page.keyboard.press("Escape")
    return found


if __name__ == "__main__":
    from linkedin.browser.registry import cli_parser, cli_session

    parser = cli_parser("Check LinkedIn connection status")
    parser.add_argument("--profile", required=True, help="Public identifier of the target profile")
    args = parser.parse_args()
    session = cli_session(args)

    test_profile = {
        "url": f"https://www.linkedin.com/in/{args.profile}/",
        "public_identifier": args.profile,
    }

    print(f"Checking connection status as {session} → {args.profile}")
    status = get_connection_status(session, test_profile)
    print(f"Connection status → {status.value}")
