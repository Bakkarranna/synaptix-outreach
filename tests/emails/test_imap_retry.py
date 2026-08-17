# tests/emails/test_imap_retry.py
"""retry_imap — the bounded retry for flaky IMAP transports, plus the mail pass
riding it. Gmail's IMAP occasionally drops a fresh login (rate-limited reconnect,
network blip); one retry absorbs the blip without turning an unreachable box into
a multi-minute hang."""
from unittest.mock import patch

import pytest

from openoutreach.emails.imap_retry import retry_imap
from tests.emails.fake_imap import FakeIMAP, message


class TestRetryImap:
    def test_succeeds_after_transient_failures(self):
        calls = {"n": 0}

        def op():
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError("read timed out")
            return "ok"

        with patch("openoutreach.emails.imap_retry.time.sleep"):
            assert retry_imap(op, attempts=3, backoff=0) == "ok"
        assert calls["n"] == 3

    def test_gives_up_and_raises_the_last_error(self):
        calls = {"n": 0}

        def op():
            calls["n"] += 1
            raise OSError("read timed out")

        with patch("openoutreach.emails.imap_retry.time.sleep"):
            with pytest.raises(OSError, match="read timed out"):
                retry_imap(op, attempts=3, backoff=0)
        assert calls["n"] == 3

    def test_non_connection_errors_propagate_immediately(self):
        """A bug in the pass itself is not a network blip — never retry it."""
        calls = {"n": 0}

        def op():
            calls["n"] += 1
            raise ValueError("nope")

        with pytest.raises(ValueError):
            retry_imap(op, attempts=3, backoff=0)
        assert calls["n"] == 1


@pytest.mark.django_db
def test_read_mail_retries_a_transient_connection_blip(campaign):
    """One dropped connection is absorbed; the pass still reads the mail."""
    from openoutreach.chat.models import ChatMessage
    from openoutreach.crm.models import DealState
    from openoutreach.emails.inbox import read_mail
    from openoutreach.emails.models import Mailbox
    from tests.factories import DealFactory, LeadFactory

    sender = "s@infra.com"
    root = "<root@infra.com>"
    box = Mailbox.objects.create(
        username=sender, password="pw", from_address=sender, daily_limit=10)
    DealFactory(
        campaign=campaign,
        lead=LeadFactory(email="p@corp.com"),
        state=DealState.EMAILED,
        mailbox=box,
        email_subject="Hi",
        email_message_id=root,
    )

    fake = FakeIMAP([message(7, to=sender, sender="p@corp.com", references=root)])
    with patch("openoutreach.emails.inbox.imaplib.IMAP4_SSL",
               side_effect=[OSError("read timed out"), fake]), \
         patch("openoutreach.emails.imap_retry.time.sleep"):
        stored, _ = read_mail(box)

    assert stored == 1
    assert ChatMessage.objects.filter(deal__mailbox=box, is_outgoing=False).count() == 1


@pytest.mark.django_db
def test_read_mail_reuses_the_authenticated_connection(campaign):
    """Two passes on the same box make one login — the reuse that stops Gmail
    throttling connection churn."""
    from openoutreach.emails.inbox import read_mail
    from openoutreach.emails.models import Mailbox

    box = Mailbox.objects.create(
        username="s@infra.com", password="pw", from_address="s@infra.com", daily_limit=10)
    fake = FakeIMAP([])
    with patch("openoutreach.emails.inbox.imaplib.IMAP4_SSL", return_value=fake) as ctor:
        read_mail(box)
        read_mail(box)
    assert ctor.call_count == 1


@pytest.mark.django_db
def test_read_mail_backs_off_after_a_fully_failed_pass(campaign):
    """When every retry fails, the next pass skips instead of re-hammering IMAP —
    a throttled or down endpoint gets a cooldown, not a fresh burst."""
    from openoutreach.emails.inbox import read_mail
    from openoutreach.emails.models import Mailbox

    box = Mailbox.objects.create(
        username="s@infra.com", password="pw", from_address="s@infra.com", daily_limit=10)
    with patch("openoutreach.emails.inbox.imaplib.IMAP4_SSL",
               side_effect=OSError("read timed out")) as ctor, \
         patch("openoutreach.emails.imap_retry.time.sleep"):
        stored, _ = read_mail(box)
        assert stored == 0
        attempts = ctor.call_count
        assert attempts == 4  # every retry exhausted
        # second call lands inside the cooldown — no further connection attempts
        stored, _ = read_mail(box)
        assert stored == 0
    assert ctor.call_count == attempts
