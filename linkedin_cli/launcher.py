"""Launch the persistent, bound LinkedIn browser that verb processes connect to.

This is the session *owner*: it launches a persistent browser (auth/cookies live
in its on-disk profile), `browser.bind()`s it to a websocket, records the endpoint
in the session registry, and stays alive. Verb processes attach as clients via
``PlaywrightCliSession``; `playwright-cli attach <name>` can attach too (e.g. for a
human to clear a checkpoint in the live browser).
"""
from __future__ import annotations

import logging
import os
import signal

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from linkedin_cli.conf import BROWSER_DEFAULT_TIMEOUT_MS, BROWSER_HEADLESS, BROWSER_SLOW_MO
from linkedin_cli.session import clear_session, write_session

logger = logging.getLogger(__name__)

LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"


def open_bound_session(name: str, *, profile_dir: str,
                       host: str = "127.0.0.1", port: int = 0) -> None:
    """Launch a persistent browser, bind it, register the endpoint, and block.

    Runs until interrupted (SIGINT/SIGTERM), then deregisters and closes the
    browser. The websocket endpoint is also printed to stdout for convenience.
    Browser-launch knobs (headed/slow-mo/timeouts) come from ``conf``; ``host``/
    ``port`` default to a localhost OS-picked port (right for many sessions in
    one container — no cross-container exposure needed).
    """
    os.makedirs(profile_dir, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            profile_dir, headless=BROWSER_HEADLESS, slow_mo=BROWSER_SLOW_MO,
        )
        context.set_default_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
        context.set_default_navigation_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
        Stealth().apply_stealth_sync(context)

        endpoint = context.browser.bind(name, host=host, port=port)["endpoint"]
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LINKEDIN_FEED_URL)

        write_session(name, endpoint, os.getpid())
        logger.info("Session %r bound at %s (profile=%s)", name, endpoint, profile_dir)
        print(endpoint, flush=True)

        try:
            signal.pause()  # block until a termination signal
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            clear_session(name)
            context.close()
            logger.info("Session %r closed", name)
