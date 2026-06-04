"""linkedin-cli — drive LinkedIn interactions inside a bound browser session.

``session open`` launches + binds a persistent browser (the session owner); the
verbs connect to it and drive LinkedIn. One session = one account; pick it with
``--session <name>`` (or ``$LINKEDIN_CLI_SESSION``).

Output contract — design decisions, kept here so they travel with the package
when ``linkedin_cli`` is split into its own repo:

* **Every verb produces a dict** — its canonical result. That one dict is both
  the ``--json`` payload and the source the human renderer summarises, so the
  two views can never drift.
* **Human-readable by default; ``--json`` on every verb for the full dict.**
  Per clig.dev ("humans first", "keep it brief, err toward less output"), the
  default is a short, scannable per-verb summary (``status`` → ``Connected``,
  ``profile`` → a few lines); ``--json`` emits the whole dict for machines.
* **No ``--out``/file flag — print to stdout, let the caller redirect.** To save
  a result: ``linkedin-cli profile alice --json > alice.json``. This matches the
  composability convention (clig.dev; ``kubectl -o``, ``aws --output``,
  ``gh --json``) and keeps the tool free of file-lifecycle concerns.
* **stdout carries only the result; logs and errors go to stderr.** Errors are an
  ``error: <type>: <message>`` line + non-zero exit (``type`` mirrors
  ``exceptions.py``). A verb that ran is exit 0 — ``message`` reports send success
  in its dict (``sent``), not via the exit code.

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

# Exception → contract error `type`, in match order.
_ERROR_TYPES = [
    (CheckpointChallengeError, "checkpoint_challenge"),
    (AuthenticationError, "authentication"),
    (ProfileInaccessibleError, "profile_inaccessible"),
    (SkipProfile, "skip_profile"),
    (ReachedConnectionLimit, "connection_limit"),
]


# ── output helpers ─────────────────────────────────────────────────

def _out(text: str) -> None:
    """Print a result line to stdout (the only thing that touches stdout)."""
    sys.stdout.write(f"{text}\n")
    sys.stdout.flush()


def _err(text: str) -> None:
    """Print a log/error line to stderr."""
    print(text, file=sys.stderr)


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


# ── human-readable rendering (the non-`--json` default) ─────────────
#
# clig.dev: "keep it brief", "err toward less output". Each verb gets a short,
# scannable summary of its result dict; `--json` always emits the full dict.

def _human_identity(result: dict) -> str:
    member = result.get("self", result)
    return f"{member.get('full_name')} ({member.get('public_identifier')})"


def _human_state(result: dict) -> str:
    return result.get("state", "")


def _human_sent(result: dict) -> str:
    return "sent" if result.get("sent") else "not sent"


def _human_profile(result: dict) -> str:
    industry = result.get("industry") or {}
    subtitle = " · ".join(x for x in (
        result.get("location_name"),
        industry.get("name") if isinstance(industry, dict) else None,
    ) if x)
    lines = [" — ".join(x for x in (result.get("full_name"), result.get("headline")) if x)]
    if subtitle:
        lines.append(subtitle)
    lines.append(f"{len(result.get('positions') or [])} positions · "
                 f"{len(result.get('educations') or [])} schools")
    lines.append("(--json for the full record)")
    return "\n".join(lines)


def _human_thread(result: dict) -> str:
    messages = result.get("messages")
    if not messages:
        return "(no conversation)"
    return "\n".join(
        f"{m.get('timestamp', '')}  {m.get('sender', '')}: {m.get('text', '')}"
        for m in messages
    )


def _human_closed(result: dict) -> str:
    return f"closed {result.get('name')}"


_HUMAN = {
    "login": _human_identity,
    "whoami": _human_identity,
    "status": _human_state,
    "connect": _human_state,
    "message": _human_sent,
    "profile": _human_profile,
    "thread": _human_thread,
    "session-close": _human_closed,
}


def _render(command: str, result: dict, as_json: bool) -> None:
    """Print *result*: the full dict as JSON if ``--json``, else a brief summary."""
    if as_json:
        _out(json.dumps(result, ensure_ascii=False, default=str))
        return
    renderer = _HUMAN.get(command)
    _out(renderer(result) if renderer
         else "\n".join(f"{k}: {v}" for k, v in result.items()))


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
    from linkedin_cli.auth import authenticate

    authenticate(session)
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
        _err(f"error: usage: no open session named {args.name!r}")
        return 2
    os.kill(record["pid"], signal.SIGTERM)
    _render("session-close", {"name": args.name, "closed": True}, args.json)
    return 0


# ── verb runner ────────────────────────────────────────────────────

def _run_verb(args) -> int:
    record = read_session(args.name)
    if not record:
        _err(f"error: usage: no open session named {args.name!r} — run "
             f"`linkedin-cli session open --session {args.name}`")
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
        _render(args.verb, _VERBS[args.verb](session, args), args.json)
        return 0
    except Exception as exc:  # noqa: BLE001 — map known errors, re-raise the rest
        error_type = _error_type(exc)
        if error_type is None:
            raise
        _err(f"error: {error_type}: {exc}")
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
    common.add_argument(
        "--json", action="store_true",
        help="Emit the full result as JSON instead of a human-readable summary",
    )

    parser = argparse.ArgumentParser(prog="linkedin-cli", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    # session open / close
    session_cmd = sub.add_parser("session", help="Manage the bound browser session")
    session_sub = session_cmd.add_subparsers(dest="subcmd", required=True)
    session_sub.add_parser("open", parents=[common], help="Launch + bind a persistent browser, then block")
    session_sub.add_parser("close", parents=[common], help="Signal the session launcher to shut down")

    # verbs
    handle_help = "Profile URL or public identifier (e.g. alice-smith)"

    sub.add_parser("login", parents=[common],
                   help="Log the session in (fill the form, clear a checkpoint) and report the logged-in member")
    sub.add_parser("whoami", parents=[common],
                   help="Report who the session is logged in as — no login, no checkpoint")

    p_profile = sub.add_parser("profile", parents=[common],
                               help="Scrape a member's full profile: headline, positions, education, location")
    p_profile.add_argument("handle", help=handle_help)
    p_profile.add_argument("--raw", action="store_true", help="Also emit the untouched Voyager blob under _raw")

    sub.add_parser("status", parents=[common],
                   help="Report the connection state with the member: Connected, Pending, or Qualified"
                   ).add_argument("handle", help=handle_help)
    sub.add_parser("connect", parents=[common],
                   help="Send a connection request (no note); no-op if already Connected or Pending"
                   ).add_argument("handle", help=handle_help)
    sub.add_parser("thread", parents=[common],
                   help="Dump the conversation with the member as a list of messages (newest last)"
                   ).add_argument("handle", help=handle_help)

    p_message = sub.add_parser("message", parents=[common],
                               help="Send a direct message to the member")
    p_message.add_argument("handle", help=handle_help)
    p_message.add_argument("--text", required=True, help="Message body to send")
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
