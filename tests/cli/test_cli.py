"""Tests for the linkedin_cli verb CLI — dispatch, error mapping, handle parsing.

The session is mocked at the boundary (no real browser): we inject a fake
session via ``read_session`` + ``PlaywrightCliSession`` so the verb runner is
exercised without Playwright.
"""
import json

import pytest

from linkedin_cli import cli
from linkedin_cli.exceptions import AuthenticationError, ProfileInaccessibleError


# ── _handle_to_profile ─────────────────────────────────────────────

def test_handle_to_profile_from_id():
    out = cli._handle_to_profile("alice-smith")
    assert out["public_identifier"] == "alice-smith"
    assert out["url"] == "https://www.linkedin.com/in/alice-smith/"


def test_handle_to_profile_from_url():
    out = cli._handle_to_profile("https://www.linkedin.com/in/alice-smith/")
    assert out["public_identifier"] == "alice-smith"


def test_handle_to_profile_rejects_non_profile_url():
    with pytest.raises(ValueError):
        cli._handle_to_profile("https://www.linkedin.com/feed/")


# ── error-type mapping ─────────────────────────────────────────────

def test_error_type_maps_known_exceptions():
    assert cli._error_type(AuthenticationError("x")) == "authentication"
    assert cli._error_type(ProfileInaccessibleError("x")) == "profile_inaccessible"


def test_error_type_unknown_returns_none():
    assert cli._error_type(ValueError("x")) is None


# ── verb runner dispatch ───────────────────────────────────────────

class _FakeSession:
    """Minimal stand-in injected in place of PlaywrightCliSession."""

    def __init__(self, *args, **kwargs):
        self.closed = False
        self._raise = None

    def ensure_browser(self):
        pass

    @property
    def self_profile(self):
        if self._raise:
            raise self._raise
        return {"public_identifier": "me-self", "urn": "urn:li:fsd_profile:ME", "full_name": "Me Self"}

    def close(self):
        self.closed = True


@pytest.fixture
def injected_session(monkeypatch):
    """Route the verb runner to a fake session and a present registry entry."""
    session = _FakeSession()
    monkeypatch.setattr(cli, "read_session", lambda name: {"endpoint": "ws://x/abc", "pid": 1})
    monkeypatch.setattr(cli, "PlaywrightCliSession", lambda *a, **k: session)
    return session


def _run(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr().out.strip()
    return code, json.loads(out)


def test_whoami_happy_path(injected_session, capsys):
    code, payload = _run(["whoami", "--session", "work"], capsys)
    assert code == 0
    assert payload == {"self": {"public_identifier": "me-self", "urn": "urn:li:fsd_profile:ME", "full_name": "Me Self"}}
    assert injected_session.closed  # session always released


def test_known_error_becomes_structured_json(injected_session, capsys):
    injected_session._raise = AuthenticationError("session expired")
    code, payload = _run(["whoami", "--session", "work"], capsys)
    assert code == 1
    assert payload["error"]["type"] == "authentication"
    assert "session expired" in payload["error"]["message"]
    assert injected_session.closed


def test_unknown_error_propagates(injected_session):
    injected_session._raise = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        cli.main(["whoami", "--session", "work"])
    assert injected_session.closed  # released even when the error propagates


def test_missing_session_is_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(cli, "read_session", lambda name: None)
    code, payload = _run(["profile", "alice", "--session", "nope"], capsys)
    assert code == 2
    assert payload["error"]["type"] == "usage"
