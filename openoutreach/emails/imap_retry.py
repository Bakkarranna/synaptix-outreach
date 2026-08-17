# openoutreach/emails/imap_retry.py
"""Bounded retry for flaky IMAP transports.

Gmail's IMAP is generally reliable but occasionally slow to answer a fresh login
— rate-limited reconnects and network blips both surface as an ``imaplib`` error
or ``OSError``, often as a read timeout on the first command. The mail pass and
the warmth measurement used to give up after the first failure, so a single blip
became a skipped pass (and, for warmth, a day without re-measuring capacity).
One bounded retry loop absorbs the blip: ``attempts`` total tries, doubling
backoff between them, re-raising the last error when every attempt fails so the
caller's existing failure handling still applies.

Only connection-level failures are retried. A completed pass that found nothing
returns normally and is never retried — retrying it would be retrying a fact.
"""

from __future__ import annotations

import imaplib
import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

# Total attempts and the base backoff between them. Four tries at 5s/10s/20s
# absorbs a Gmail throttle window — measured on this install, a throttled login
# answers 10–45s late or not at all, so three quick tries was not enough. The
# caller (mail pass / warmth) still bounds the worst case, and neither pass
# re-advances anything until a full attempt succeeds, so a retry is always
# idempotent.
IMAP_ATTEMPTS = 4
IMAP_RETRY_BACKOFF_S = 5.0

_T = TypeVar("_T")


def retry_imap(operation: Callable[[], _T], *, attempts: int = IMAP_ATTEMPTS,
               backoff: float = IMAP_RETRY_BACKOFF_S) -> _T:
    """Run *operation* until it succeeds or *attempts* tries have failed.

    Retries only on connection-level failures (``imaplib.IMAP4.error``,
    ``OSError``); anything else propagates immediately. Re-raises the last error
    when every attempt fails, so callers keep their existing failure handling.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except (imaplib.IMAP4.error, OSError) as exc:
            last = exc
            if attempt + 1 < attempts:
                delay = backoff * (2 ** attempt)
                logger.warning("imap attempt %d/%d failed (%s) — retrying in %.0fs",
                               attempt + 1, attempts, exc, delay)
                time.sleep(delay)
    assert last is not None
    raise last
