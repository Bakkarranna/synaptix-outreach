# tests/emails/test_mailbox.py
"""MailboxManager.create_verified: SMTP auth is the gate for storing a box."""
import pytest
from unittest.mock import patch

from openoutreach.emails.models import Mailbox

_FIELDS = dict(
    from_address="joe@acme.com",
    password="app-pw",
    host="smtp.acme.com",
    port=465,
    imap_host="imap.acme.com",
    imap_port=993,
)


@pytest.mark.django_db
def test_create_verified_stores_box_when_auth_succeeds():
    with patch("openoutreach.emails.smtp.verify_auth", return_value=(True, "")) as auth:
        box, reason = Mailbox.objects.create_verified(**_FIELDS)

    auth.assert_called_once_with("smtp.acme.com", 465, "joe@acme.com", "app-pw")
    assert reason == ""
    assert box is not None
    # the SMTP login is the address itself, and every field round-trips
    assert box.username == "joe@acme.com"
    assert box.from_address == "joe@acme.com"
    assert (box.host, box.port, box.imap_host, box.imap_port) == (
        "smtp.acme.com", 465, "imap.acme.com", 993,
    )
    assert Mailbox.objects.count() == 1


@pytest.mark.django_db
def test_create_verified_supports_split_login_alias_case():
    """A provider mailbox sending as one of its own aliases: SMTP logs in as the
    primary mailbox while From: stays the alias (Hostinger info@ → outreach@)."""
    fields = {
        **_FIELDS,
        "from_address": "outreach@acme.com",
        "smtp_username": "info@acme.com",
    }
    with patch("openoutreach.emails.smtp.verify_auth", return_value=(True, "")) as auth:
        box, reason = Mailbox.objects.create_verified(**fields)

    auth.assert_called_once_with("smtp.acme.com", 465, "info@acme.com", "app-pw")
    assert reason == ""
    assert box is not None
    # the login is the primary mailbox; the From: header is the alias
    assert box.username == "info@acme.com"
    assert box.from_address == "outreach@acme.com"


@pytest.mark.django_db
def test_create_verified_stores_nothing_when_auth_rejected():
    with patch("openoutreach.emails.smtp.verify_auth", return_value=(False, "auth rejected (535)")):
        box, reason = Mailbox.objects.create_verified(**_FIELDS)

    assert box is None
    assert reason == "auth rejected (535)"
    assert Mailbox.objects.count() == 0


@pytest.mark.django_db
def test_create_verified_verifies_separate_imap_credentials():
    """A box with distinct read-side credentials is IMAP-auth-checked too."""
    fields = {
        **_FIELDS,
        "imap_username": "bot@inbox.example",
        "imap_password": "imap-pw",
    }
    with (
        patch("openoutreach.emails.smtp.verify_auth", return_value=(True, "")),
        patch("openoutreach.emails.inbox.verify_imap", return_value=(True, "")) as imap,
    ):
        box, reason = Mailbox.objects.create_verified(**fields)

    imap.assert_called_once_with("imap.acme.com", 993, "bot@inbox.example", "imap-pw")
    assert reason == ""
    assert box is not None
    assert box.effective_imap_username == "bot@inbox.example"
    assert box.effective_imap_password == "imap-pw"


@pytest.mark.django_db
def test_create_verified_rejects_box_when_imap_auth_fails():
    """A bad read-side credential fails at connect time, storing nothing."""
    fields = {
        **_FIELDS,
        "imap_username": "bot@inbox.example",
        "imap_password": "bad-pw",
    }
    with (
        patch("openoutreach.emails.smtp.verify_auth", return_value=(True, "")),
        patch(
            "openoutreach.emails.inbox.verify_imap",
            return_value=(False, "imap auth rejected: 535"),
        ),
    ):
        box, reason = Mailbox.objects.create_verified(**fields)

    assert box is None
    assert reason == "imap auth rejected: 535"
    assert Mailbox.objects.count() == 0


@pytest.mark.django_db
def test_create_verified_without_imap_overrides_keeps_single_credential():
    """No IMAP overrides -> the read side falls back to the SMTP login, as before."""
    with patch("openoutreach.emails.smtp.verify_auth", return_value=(True, "")):
        box, _ = Mailbox.objects.create_verified(**_FIELDS)

    assert box.effective_imap_username == "joe@acme.com"
    assert box.effective_imap_password == "app-pw"


@pytest.mark.django_db
def test_manual_pause_zeroes_headroom():
    """The operator's own pause stops first emails whatever the measurement says."""
    box = Mailbox.objects.create(
        username="joe@acme.com", password="pw", from_address="joe@acme.com",
        daily_limit=10,
    )
    assert box.headroom_today() == 10
    box.send_paused = True
    box.save(update_fields=["send_paused"])
    assert box.headroom_today() == 0


@pytest.mark.django_db
def test_create_verified_repairs_existing_box_in_place():
    with patch("openoutreach.emails.smtp.verify_auth", return_value=(True, "")):
        Mailbox.objects.create_verified(**_FIELDS)
        box, _ = Mailbox.objects.create_verified(**{**_FIELDS, "password": "new-pw"})

    assert Mailbox.objects.count() == 1
    assert box.password == "new-pw"
