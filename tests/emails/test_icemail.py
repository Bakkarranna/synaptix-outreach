# tests/emails/test_icemail.py
"""Parse the IceMail Export-Mailboxes paste — fixed columns, no DB."""
import pytest

from openoutreach.emails.icemail import parse_mailboxes

HEADER = "Email\tPassword\tRecovery Email\tService Provider\tAdmin"


def test_parses_real_export_row():
    pasted = f"{HEADER}\neracle@indieoutreach.app\tp7g&gwKYWRX1\tteam@icemail.ai\tGOOGLE\t1"
    assert parse_mailboxes(pasted) == [("eracle@indieoutreach.app", "p7g&gwKYWRX1")]


def test_tolerates_column_reorder():
    pasted = "Password\tEmail\nsecret123\tjoe@acme.com"
    assert parse_mailboxes(pasted) == [("joe@acme.com", "secret123")]


def test_csv_delimiter_fallback():
    pasted = "Email,Password\njane@acme.com,pw"
    assert parse_mailboxes(pasted) == [("jane@acme.com", "pw")]


def test_skips_blank_and_incomplete_rows():
    pasted = f"{HEADER}\n\njoe@acme.com\tpw\tx\tGOOGLE\t1\n\t\t\t\t"
    assert parse_mailboxes(pasted) == [("joe@acme.com", "pw")]


def test_missing_header_raises():
    with pytest.raises(ValueError):
        parse_mailboxes("joe@acme.com\tpw")


def test_empty_paste_is_empty():
    assert parse_mailboxes("   ") == []
