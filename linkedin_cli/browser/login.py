# linkedin/browser/login.py
import logging
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from termcolor import colored

from linkedin_cli.browser.nav import goto_page, human_type, resolve_locator
from linkedin_cli.conf import (
    BROWSER_DEFAULT_TIMEOUT_MS,
    BROWSER_LOGIN_TIMEOUT_MS,
    BROWSER_SLOW_MO,
    CHECKPOINT_RESOLVE_TIMEOUT_S,
)
from linkedin_cli.page_state import PageState, classify_page

CHECKPOINT_POLL_S = 5

logger = logging.getLogger(__name__)

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"

EMAIL_LOCATORS = [
    lambda p: p.get_by_role("textbox", name="Email or phone"),
    lambda p: p.get_by_label("Email or phone"),
    lambda p: p.locator('input[autocomplete="webauthn"]'),
    lambda p: p.locator('input[name="session_key"]'),
    lambda p: p.locator('input#username'),
    lambda p: p.locator('form input[type="text"]'),
]

PASSWORD_LOCATORS = [
    lambda p: p.locator('input[type="password"]'),
    lambda p: p.locator('input[autocomplete="current-password"]'),
    lambda p: p.get_by_role("textbox", name="Password"),
    lambda p: p.get_by_label("Password"),
    lambda p: p.locator('input[name="session_password"]'),
    lambda p: p.locator('input#password'),
]

SUBMIT_LOCATORS = [
    lambda p: p.locator("form").get_by_role("button", name="Sign in", exact=True),
    lambda p: p.get_by_role("button", name="Sign in", exact=True),
    lambda p: p.locator('form button[type="submit"]'),
    lambda p: p.locator('button[type="submit"]'),
]

COMPLY_LOCATORS = [
    lambda p: p.locator('button#content__button--primary--muted'),
    lambda p: p.get_by_role("button", name="Agree to comply", exact=True),
    lambda p: p.locator('button.content__button--primary'),
]

COMPLY_PROBE_TIMEOUT_MS = 5000


def dismiss_comply_gate(page, timeout_ms: int = COMPLY_PROBE_TIMEOUT_MS) -> bool:
    """Click LinkedIn's 'Agree to comply' interstitial if present. Return True if clicked."""
    for factory in COMPLY_LOCATORS:
        locator = factory(page).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            continue
        logger.info(colored("Dismissing 'Agree to comply' interstitial", "yellow"))
        locator.click()
        return True
    return False


def await_checkpoint_clear(page, timeout_s: int = CHECKPOINT_RESOLVE_TIMEOUT_S) -> bool:
    """Block while the user clears a LinkedIn checkpoint in the live browser.

    The browser runs headed (noVNC at http://localhost:6080/vnc.html), so the
    user can solve the challenge by hand. Returns True once the page leaves
    ``/checkpoint/``, or False if it is still there after *timeout_s*. We never
    resubmit credentials — every automated retry hardens the block; the only
    escape is a human.
    """
    banner = "*" * 64
    logger.error(colored(banner, "red", attrs=["bold"]))
    logger.error(colored("  RESOLVE CHECKPOINT  ".center(64, "*"), "red", attrs=["bold"]))
    logger.error(colored(banner, "red", attrs=["bold"]))
    logger.error(
        colored(
            "Clear the challenge by hand in the live browser:",
            "red", attrs=["bold"],
        )
    )
    logger.error("Open the browser here: http://localhost:6080/vnc.html")
    logger.error(f"Checkpoint URL: {page.url}")
    logger.error(colored(banner, "red", attrs=["bold"]))
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if classify_page(page) is not PageState.CHECKPOINT:
            logger.info(colored("Checkpoint cleared — continuing", "green", attrs=["bold"]))
            return True
        time.sleep(CHECKPOINT_POLL_S)
    return False


def submit_login_form(session, username, password):
    """Fill and submit LinkedIn's login form (credentials supplied by the caller).

    Does *not* assert the outcome — the caller (the auth flow's ``@transition``)
    re-reads the page to decide what the submit produced: the feed, a checkpoint,
    or, on rejected credentials, the login page again.
    """
    page = session.page
    logger.info(colored("Submitting login form", "cyan") + f" for {session}")

    goto_page(
        session,
        action=lambda: page.goto(LINKEDIN_LOGIN_URL),
        expected_url_pattern="/login",
        error_message="Failed to load login page",
    )

    human_type(resolve_locator(page, EMAIL_LOCATORS), username)
    session.wait()
    human_type(resolve_locator(page, PASSWORD_LOCATORS), password)
    session.wait()

    resolve_locator(page, SUBMIT_LOCATORS).click()
    dismiss_comply_gate(page)
    page.wait_for_load_state("domcontentloaded", timeout=BROWSER_LOGIN_TIMEOUT_MS)


def launch_browser(storage_state=None):
    logger.debug("Launching Playwright")
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=False, slow_mo=BROWSER_SLOW_MO)
    context = browser.new_context(storage_state=storage_state)
    context.set_default_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
    context.set_default_navigation_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
    Stealth().apply_stealth_sync(context)
    page = context.new_page()
    return page, context, browser, playwright
