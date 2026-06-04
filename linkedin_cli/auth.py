"""Drive a LinkedIn browser session to the authenticated feed.

The auth flow is declared, not coded: each step is a ``@auth_flow.transition``
action annotated with the page state it runs *from* and the states it may legally
*produce*. The generic :meth:`PageFlow.run` loop (in ``page_state``) does the
driving — observe the live page, dispatch to the action for that state, repeat
until the feed. There is no hand-written loop or dispatch table here.

Both the standalone CLI (``linkedin-cli login``) and the daemon
(``linkedin/browser/launch.py``) call :func:`authenticate`, so the two share one
enforced flow instead of hand-rolling their own login sequences.
"""
from __future__ import annotations

import logging

from termcolor import colored

from linkedin_cli.browser.login import (
    LINKEDIN_LOGIN_URL,
    await_checkpoint_clear,
    submit_login_form,
)
from linkedin_cli.exceptions import (
    AuthenticationError,
    CheckpointChallengeError,
    IllegalPageTransition,
)
from linkedin_cli.page_state import PageFlow, PageState

logger = logging.getLogger(__name__)

LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"

auth_flow = PageFlow("auth", goal=PageState.FEED)


@auth_flow.transition(
    when=PageState.UNKNOWN,
    then={PageState.LOGIN, PageState.FEED, PageState.AUTHWALL, PageState.CHECKPOINT},
)
def _from_unknown(session) -> None:
    """Blank/unknown page → head to the feed and let LinkedIn route us."""
    session.page.goto(LINKEDIN_FEED_URL)
    session.page.wait_for_load_state("domcontentloaded")


@auth_flow.transition(when=PageState.AUTHWALL, then={PageState.LOGIN})
def _from_authwall(session) -> None:
    """Guest authwall → go to the login form."""
    session.page.goto(LINKEDIN_LOGIN_URL)
    session.page.wait_for_load_state("domcontentloaded")


@auth_flow.transition(when=PageState.LOGIN, then={PageState.FEED, PageState.CHECKPOINT})
def _from_login(session) -> None:
    """Login form → submit credentials.

    Landing back on the login page (rejected credentials) is outside the declared
    ``then`` and so raises — which also enforces the never-resubmit rule: every
    credential resubmit hardens LinkedIn's block, so we try exactly once.
    """
    if not getattr(session, "username", None):
        raise AuthenticationError(
            "Not logged in and no LINKEDIN_USERNAME/LINKEDIN_PASSWORD provided"
        )
    submit_login_form(session, session.username, session.password)


@auth_flow.transition(when=PageState.CHECKPOINT, then={PageState.FEED})
def _from_checkpoint(session) -> None:
    """Checkpoint challenge → wait for a human to clear it in the live browser."""
    if not await_checkpoint_clear(session.page):
        raise CheckpointChallengeError(session.page.url)


def authenticate(session, *, username=None, password=None) -> None:
    """Drive *session* to the authenticated feed, or raise.

    Credentials, when given, are stamped onto the session (the daemon passes them
    explicitly; the standalone CLI lets the session carry them from the
    environment), then the ``auth_flow`` drives to the feed.

    Raises :class:`AuthenticationError` if the feed can't be reached (no action
    for the current page, rejected credentials, or too many hops) and
    :class:`CheckpointChallengeError` if a challenge can't be cleared in time.
    """
    if username is not None:
        session.username = username
    if password is not None:
        session.password = password

    try:
        auth_flow.run(session)
    except IllegalPageTransition as exc:
        raise AuthenticationError(str(exc)) from exc

    logger.info(colored("Authenticated — on the feed", "green", attrs=["bold"]))
