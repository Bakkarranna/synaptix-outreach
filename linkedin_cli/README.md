# linkedin-cli

A **Django-free** library and CLI for LinkedIn *platform mechanics* — browser
navigation, the Voyager API, profile/conversation scraping, and the
connect/message/status/thread actions — driven against a **bound browser
session**. It owns no business logic (campaigns, CRM, ML) and no database; it
just knows about a LinkedIn page and a browser.

It currently lives inside the [OpenOutreach](https://github.com/eracle/OpenOutreach)
repo and is destined for its own repo + PyPI package; this README travels with it.

## Install

```bash
pip install playwright playwright-stealth termcolor
python -m playwright install chromium
```

## Session model — bind + connect

One process (the **session owner**) launches a persistent browser and
`browser.bind()`s it under a name; every verb is a short-lived client that
`chromium.connect()`s to that same browser. Auth/cookies/fingerprint live in the
owner's on-disk profile — **the CLI keeps no DB**, only a name→endpoint registry.
One session = one LinkedIn account.

```bash
# 1. Open + bind a session (blocks; it owns the browser). Run once.
python -m linkedin_cli.cli session open --session work

# 2. Drive it from other processes. Pick the session per-call or once via env:
export LINKEDIN_CLI_SESSION=work
python -m linkedin_cli.cli login            # creds from $LINKEDIN_USERNAME/$LINKEDIN_PASSWORD
python -m linkedin_cli.cli profile alice-smith
python -m linkedin_cli.cli session close
```

`playwright-cli attach work` can attach to the same browser (e.g. for a human to
clear a checkpoint by hand in the live window).

## Output contract

The canonical statement lives in the `cli.py` module docstring; in short:

- **Every verb produces a result dict.** That one dict is both the `--json`
  payload and the source the human renderer summarises — the two never drift.
- **Human-readable by default; `--json` on every verb** for the full dict.
  Per [clig.dev](https://clig.dev/) ("humans first", "keep it brief"), the
  default is a short, scannable summary.
- **No `--out` flag — print to stdout, redirect to save:**
  `linkedin-cli profile alice --json > alice.json` (composability convention,
  cf. `kubectl -o`, `aws --output`, `gh --json`).
- **stdout carries only the result; logs/errors go to stderr** as
  `error: <type>: <message>` + non-zero exit. A verb that ran is exit 0.

## Verbs

`--session <name>` (or `$LINKEDIN_CLI_SESSION`) and `--json` apply to every verb.

| Verb | Human default | `--json` result dict |
|---|---|---|
| `login` | `Alice Smith (alice-smith)` | `{account, self:{public_identifier, urn, full_name}}` |
| `whoami` | `Alice Smith (alice-smith)` | `{self:{public_identifier, urn, full_name}}` |
| `profile <id>` | name — headline / location · industry / N positions · M schools | full `LinkedInProfile` (positions[], educations[], geo, …); `--raw` adds `_raw` |
| `status <id>` | `Connected` | `{public_identifier, state}` — `Connected`/`Pending`/`Qualified` |
| `connect <id>` | `Pending` | `{public_identifier, state}` — no note; no-op if already Connected/Pending |
| `message <id> --text …` | `sent` / `not sent` | `{public_identifier, sent}` |
| `thread <id>` | one line per message (newest last) | `{public_identifier, messages:[{sender, text, timestamp}]}` |

A `<id>` is a public identifier (`alice-smith`) or a profile URL. Verbs that need
the member `urn` (`message`/`thread`/`status`) resolve it from the handle.

## Auth — a page-state machine

Login is not a one-shot form-fill; LinkedIn can redirect to a login, authwall, or
checkpoint at any point. So auth is modelled as an **observed** page-state machine
(`page_state.py`, `auth.py`):

- `classify_page(page)` judges the live page by **URL path only** — a
  `/login?session_redirect=…/feed/` redirect must not read as the feed.
- `@transition(when=, then=)` is a contract decorator on each action: it enforces
  the precondition state *and*, by re-reading the page after the action, that the
  result is one of the allowed `then` states — raising `IllegalPageTransition`
  otherwise (the postcondition is what a held-state FSM can't express).
- `PageFlow.run()` is the generic observe→act loop; `authenticate()` drives
  login/authwall/checkpoint → feed, shared by the CLI and the OpenOutreach daemon.

## Errors

Mapped to a stable `type` (mirrors `exceptions.py`): `checkpoint_challenge`,
`authentication`, `profile_inaccessible`, `skip_profile`, `connection_limit`.
Printed as `error: <type>: <message>` on stderr with a non-zero exit.
