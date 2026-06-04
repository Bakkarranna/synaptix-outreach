"""linkedin-cli — drive LinkedIn interactions inside a bound browser session.

``session open`` launches + binds a persistent browser (the session owner); the
verbs connect to it and emit structured JSON on **stdout** (human logs go to
**stderr**, so piping stdout stays clean). One session = one account; pick it
with ``--session <name>``.

This module is the composition root: it owns policy (e.g. interaction pacing)
and injects it into the session — the session/action layers read no config.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
from urllib.parse import unquote

from linkedin_cli.enums import ProfileState
from linkedin_cli.exceptions import (
    AuthenticationError,
    CheckpointChallengeError,
    ProfileInaccessibleError,
    ReachedConnectionLimit,
    SkipProfile,
)
from linkedin_cli.session import PlaywrightCliSession, linkedin_cli_home, read_session
from linkedin_cli.url_utils import public_id_to_url, url_to_public_id

logger = logging.getLogger("linkedin_cli")

# Pacing policy lives here (the composition root), injected into the session.
DEFAULT_MIN_PACE_S = 5.0
DEFAULT_MAX_PACE_S = 8.0

LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"

# Exception → contract error `type`, in match order.
_ERROR_TYPES = [
    (CheckpointChallengeError, "checkpoint_challenge"),
    (AuthenticationError, "authentication"),
    (ProfileInaccessibleError, "profile_inaccessible"),
    (SkipProfile, "skip_profile"),
    (ReachedConnectionLimit, "connection_limit"),
]


# ── output helpers ─────────────────────────────────────────────────

def _emit(obj) -> None:
    """Write one JSON object to stdout (the only thing that touches stdout)."""
    json.dump(obj, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _error_type(exc: Exception) -> str | None:
    for cls, name in _ERROR_TYPES:
        if isinstance(exc, cls):
            return name
    return None


def _self_block(profile: dict) -> dict:
    return {
        "public_identifier": profile.get("public_identifier"),
        "urn": profile.get("urn"),
        "full_name": profile.get("full_name"),
    }


def _handle_to_profile(handle: str) -> dict:
    """Build a minimal ``{public_identifier, url}`` from a <url|id> handle."""
    public_id = url_to_public_id(handle) if "/" in handle else handle
    if not public_id:
        raise ValueError(f"Could not resolve a public identifier from {handle!r}")
    return {"public_identifier": public_id, "url": public_id_to_url(public_id)}


def _scrape(session, handle: str) -> dict:
    """Scrape the target so urn-dependent verbs (message/thread) have its ``urn``."""
    from linkedin_cli.actions.profile import scrape_profile

    profile, _data = scrape_profile(session, _handle_to_profile(handle))
    if not profile:
        raise ProfileInaccessibleError(handle)
    return profile


# ── verbs ──────────────────────────────────────────────────────────

def _verb_login(session, args) -> dict:
    session.ensure_browser()
    session.page.goto(LINKEDIN_FEED_URL)
    if "/feed" not in unquote(session.page.url):
        if not session.username:
            raise AuthenticationError(
                "Not logged in and no LINKEDIN_USERNAME/LINKEDIN_PASSWORD provided"
            )
        from linkedin_cli.browser.login import playwright_login
        playwright_login(session, session.username, session.password)
    return {"account": args.name, "self": _self_block(session.self_profile)}


def _verb_whoami(session, args) -> dict:
    return {"self": _self_block(session.self_profile)}


def _verb_profile(session, args) -> dict:
    from linkedin_cli.actions.profile import scrape_profile

    profile, data = scrape_profile(session, _handle_to_profile(args.handle))
    if not profile:
        raise ProfileInaccessibleError(args.handle)
    out = dict(profile)
    if args.raw:
        out["_raw"] = data
    return out


def _verb_status(session, args) -> dict:
    from linkedin_cli.actions.status import get_connection_status

    profile = _handle_to_profile(args.handle)
    state = get_connection_status(session, profile)
    return {"public_identifier": profile["public_identifier"], "state": state.value}


def _verb_connect(session, args) -> dict:
    from linkedin_cli.actions.connect import send_connection_request
    from linkedin_cli.actions.status import get_connection_status

    profile = _handle_to_profile(args.handle)
    state = get_connection_status(session, profile)
    if state not in (ProfileState.CONNECTED, ProfileState.PENDING):
        state = send_connection_request(session, profile)
    return {"public_identifier": profile["public_identifier"], "state": state.value}


def _verb_message(session, args) -> dict:
    from linkedin_cli.actions.message import send_raw_message

    profile = _scrape(session, args.handle)
    sent = send_raw_message(session, profile, args.text)
    return {"public_identifier": profile.get("public_identifier"), "sent": sent}


def _verb_thread(session, args) -> dict:
    from linkedin_cli.actions.conversations import get_conversation

    profile = _scrape(session, args.handle)
    messages = get_conversation(session, profile.get("urn"), session.self_profile["urn"])
    return {"public_identifier": profile.get("public_identifier"), "messages": messages}


_VERBS = {
    "login": _verb_login,
    "whoami": _verb_whoami,
    "profile": _verb_profile,
    "status": _verb_status,
    "connect": _verb_connect,
    "message": _verb_message,
    "thread": _verb_thread,
}


# ── session lifecycle commands ─────────────────────────────────────

def _cmd_session_open(args) -> int:
    from linkedin_cli.launcher import open_bound_session

    profile_dir = str(linkedin_cli_home() / "profiles" / args.name)
    open_bound_session(args.name, profile_dir=profile_dir)
    return 0


def _cmd_session_close(args) -> int:
    record = read_session(args.name)
    if not record:
        _emit({"error": {"type": "usage", "message": f"No open session named {args.name!r}"}})
        return 2
    os.kill(record["pid"], signal.SIGTERM)
    _emit({"name": args.name, "closed": True})
    return 0


# ── verb runner ────────────────────────────────────────────────────

def _run_verb(args) -> int:
    record = read_session(args.name)
    if not record:
        _emit({"error": {"type": "usage", "message":
               f"No open session named {args.name!r} — run "
               f"`linkedin-cli session open --session {args.name}`"}})
        return 2

    session = PlaywrightCliSession(
        record["endpoint"],
        min_pace=DEFAULT_MIN_PACE_S,
        max_pace=DEFAULT_MAX_PACE_S,
        username=os.environ.get("LINKEDIN_USERNAME"),
        password=os.environ.get("LINKEDIN_PASSWORD"),
        name=args.name,
    )
    try:
        session.ensure_browser()
        _emit(_VERBS[args.verb](session, args))
        return 0
    except Exception as exc:  # noqa: BLE001 — map known errors, re-raise the rest
        error_type = _error_type(exc)
        if error_type is None:
            raise
        _emit({"error": {"type": error_type, "message": str(exc)}})
        return 1
    finally:
        session.close()


# ── parser ─────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--session", "--name", dest="name",
        default=os.environ.get("LINKEDIN_CLI_SESSION", "default"),
        help="Bound session name (default: $LINKEDIN_CLI_SESSION or 'default')",
    )

    parser = argparse.ArgumentParser(prog="linkedin-cli", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    # session open / close
    session_cmd = sub.add_parser("session", help="Manage the bound browser session")
    session_sub = session_cmd.add_subparsers(dest="subcmd", required=True)
    session_sub.add_parser("open", parents=[common], help="Launch + bind a persistent browser, then block")
    session_sub.add_parser("close", parents=[common], help="Signal the session launcher to shut down")

    # verbs
    sub.add_parser("login", parents=[common], help="Authenticate the session and discover the logged-in member")
    sub.add_parser("whoami", parents=[common], help="Identity of the already-authenticated session")
    p_profile = sub.add_parser("profile", parents=[common], help="Scrape a profile → structured JSON")
    p_profile.add_argument("handle", help="Profile URL or public identifier")
    p_profile.add_argument("--raw", action="store_true", help="Also emit the untouched Voyager blob under _raw")
    for verb in ("status", "connect", "thread"):
        sub.add_parser(verb, parents=[common], help=f"{verb} a profile").add_argument(
            "handle", help="Profile URL or public identifier")
    p_message = sub.add_parser("message", parents=[common], help="Send a message")
    p_message.add_argument("handle", help="Profile URL or public identifier")
    p_message.add_argument("--text", required=True, help="Message body")
    return parser


def _configure_logging() -> None:
    level = os.environ.get("LINKEDIN_CLI_LOG", "INFO").upper()
    logging.basicConfig(level=level, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging()

    if args.cmd == "session":
        return _cmd_session_open(args) if args.subcmd == "open" else _cmd_session_close(args)

    args.verb = args.cmd
    return _run_verb(args)


if __name__ == "__main__":
    raise SystemExit(main())
