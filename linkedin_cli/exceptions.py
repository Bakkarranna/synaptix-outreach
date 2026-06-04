class AuthenticationError(Exception):
    """Custom exception for 401 Unauthorized errors."""
    pass


class TerminalStateError(Exception):
    """Profile is already done or dead — caller must skip it"""
    pass


class SkipProfile(Exception):
    """Profile must be skipped."""
    pass


class ProfileInaccessibleError(Exception):
    """Profile is private, deleted, or restricted (HTTP 403/404)."""
    pass


class ReachedConnectionLimit(Exception):
    """ Weekly connection limit reached. """
    pass


class IllegalPageTransition(Exception):
    """An action ran from, or produced, a page state its @transition contract forbids.

    Raised by the ``transition`` decorator (see ``page_state``) when the live
    page violates the action's declared precondition (``when``) or postcondition
    (``then``) — e.g. submitting credentials and landing back on the login page
    (rejected creds) instead of the feed or a checkpoint.
    """
    pass


class CheckpointChallengeError(Exception):
    """LinkedIn flagged the account with a security checkpoint.

    Carries the challenge URL so the user knows where to go to clear it.
    Raised from the login flow; the daemon must NOT call reauthenticate()
    when it sees this — that just hardens the block.
    """
    def __init__(self, url: str):
        self.url = url
        super().__init__(f"LinkedIn checkpoint challenge: {url}")

