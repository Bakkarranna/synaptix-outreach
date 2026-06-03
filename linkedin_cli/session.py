"""The session contract every linkedin_cli verb runs against.

linkedin_cli owns no browser lifecycle and no persistence. Each verb is handed
a *session* — an object that exposes a live Playwright page/context plus a few
lifecycle hooks — and drives LinkedIn through it. The concrete session is the
caller's job: OpenOutreach's daemon backs it with its Django ``AccountSession``;
the standalone CLI backs it with a Playwright CLI session adapter.

``LinkedInSession`` is the typed boundary between the two — it lists exactly what
the platform code touches, and nothing about campaigns, leads, or the DB.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from playwright.sync_api import BrowserContext, Page


@runtime_checkable
class LinkedInSession(Protocol):
    """Browser session a linkedin_cli verb attaches to.

    Implementations own browser launch, the persistent profile, auth/cookies,
    and fingerprint — none of which live here. The verbs only ever read
    ``page``/``context``, resolve their own identity via ``self_profile``, and
    call the lifecycle hooks below.
    """

    #: Live Playwright page for the authenticated session.
    page: Page
    #: Browser context owning the page (cookies, response listeners, storage).
    context: BrowserContext

    @property
    def self_profile(self) -> dict:
        """The logged-in member's own profile dict (the messaging mailbox).

        Resolved once and kept warm for the session; carries at least
        ``urn``, ``first_name``, ``last_name``.
        """
        ...

    def ensure_browser(self) -> None:
        """Launch or recover the browser so ``page`` is usable. Idempotent."""
        ...

    def wait(self, min_delay: float = ..., max_delay: float = ...) -> None:
        """Human-paced pause, then block until the page reaches DOM-ready."""
        ...

    def close(self) -> None:
        """Release browser resources held by the session."""
        ...
