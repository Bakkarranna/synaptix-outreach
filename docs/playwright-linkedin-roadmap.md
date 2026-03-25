# playwright-linkedin — Extraction Roadmap

## What

Extract `linkedin/api/` + `linkedin/browser/` + `linkedin/actions/` into a standalone `playwright-linkedin` library. Anyone can `pip install playwright-linkedin` and script their LinkedIn without building a full outreach tool.

## Architecture

```
OpenOutreach campaign logic (when/who/why)
        |
playwright-linkedin Python API (LinkedIn primitives)
        |
Playwright browser (managed by library)
        |
LinkedIn
```

The library owns the browser lifecycle. The caller passes credentials and optionally cookies as strings. No files, no config, no Django.

## Interface

### Python API

```python
from playwright_linkedin import connect_session, export_cookies

# Cold start with credentials — logs in automatically
session = connect_session(username="...", password="...")

# Cold start with cookies — skips login if valid, falls back to credentials
session = connect_session(
    username="...",
    password="...",
    storage_state={"cookies": [...]},
)

# Read
from playwright_linkedin import (
    get_profile, get_self_profile,
    fetch_conversations, get_conversation,
    get_connection_status,
)
me = get_self_profile(session)
profile = get_profile(session, public_identifier="...")
status = get_connection_status(session, profile)
conversations = fetch_conversations(session)
messages = get_conversation(session, public_identifier="...")

# Write
from playwright_linkedin import send_message, send_connection_request
send_message(session, urn="...", text="...")
send_connection_request(session, profile)

# Navigation
from playwright_linkedin import visit_profile, search_people
visit_profile(session, public_identifier="...")
search_people(session, keyword="...", page=1)

# Export cookies for next cold start
cookies = export_cookies(session)  # -> dict
```

### CLI

```bash
# Login (cookies valid -> skip, expired -> automated login with credentials)
linkedin login --username "..." --password "..." --cookies '...'

# Read
linkedin self-profile
linkedin profile --id "john-doe"
linkedin connection-status --id "john-doe"
linkedin conversations list
linkedin messages --id "john-doe"

# Write
linkedin send --urn "..." --text "..."
linkedin connect --id "john-doe"

# Navigation
linkedin search "data engineer" --page 1

# Dump cookies to stdout for persistence by caller
linkedin export-cookies
```

Cookies are strings passed by the caller. The library never reads or writes files. The caller decides where to store them (DB, env var, secret manager, shell variable).

## What moves to the library

| Module | Notes |
|---|---|
| `linkedin/api/client.py` | Core API client. `timeout_ms` is a constructor param (no conf import). Uses `url_utils` (library-internal) |
| `linkedin/api/voyager.py` | Pure parsing, zero coupling — moves as-is |
| `linkedin/api/messaging/` | Self-contained, depends only on `api/client` |
| `linkedin/api/newsletter.py` | Uses `requests` directly — moves as-is |
| `linkedin/browser/login.py` | Automated login with human-like typing, cookie management |
| `linkedin/browser/session.py` | Thin session object (page + context + browser) — strip Django parts |
| `linkedin/browser/nav.py` | `goto_page`, `human_type`, `find_top_card` — **without** `_discover_and_enrich` |
| `linkedin/actions/profile.py` | `scrape_profile` — pure API wrapper |
| `linkedin/actions/message.py` | `send_raw_message` — multi-path send (API → popup → thread). No CRM imports |
| `linkedin/actions/connect.py` | `send_connection_request` — UI-driven, no DB coupling |
| `linkedin/actions/status.py` | `get_connection_status` — UI inspection + API degree check |
| `linkedin/actions/search.py` | `visit_profile`, `search_people` — navigation helpers |
| `linkedin/actions/conversations.py` | `get_conversation`, `find_conversation_urn`. No CRM imports (CLI uses `--urn` directly) |
| `linkedin/conf.py` (subset) | Browser constants only (`BROWSER_*`, `HUMAN_TYPE_*`). `VOYAGER_REQUEST_TIMEOUT_MS` already moved to `api/client.py` |
| `linkedin/exceptions.py` | `AuthenticationError`, `SkipProfile`, `ReachedConnectionLimit` |
| Stealth | Applied by library on session creation |

## What stays in OpenOutreach

| Concern | Notes |
|---|---|
| Campaign logic | Task queue, state machine, scheduling |
| Django models | Lead, Deal, Campaign, LinkedInProfile |
| Passive profile discovery | `_discover_and_enrich()` from nav.py — OpenOutreach hooks into navigation events, not the library |
| Cookie persistence | `LinkedInProfile.cookie_data` — calls `export_cookies()` and stores the dict |
| Credential storage | `LinkedInProfile.linkedin_username/password` |
| ML pipeline | GPR, BALD, LLM qualification |
| `linkedin/db/` | All DB access layers |

## Coupling to cut

| Current import | In module | Resolution | Status |
|---|---|---|---|
| `linkedin.conf` (timeouts) | `api/client.py` | `VOYAGER_REQUEST_TIMEOUT_MS` → constructor param on `PlaywrightLinkedinAPI` | **Done** |
| `linkedin.db.urls.url_to_public_id` | `api/client.py`, `browser/nav.py`, + 10 others | Moved to `linkedin/url_utils.py` (pure utility, no DB package) | **Done** |
| `linkedin.db.leads` | `browser/nav.py` | `_discover_and_enrich` was never in nav.py — nav.py only used `url_to_public_id` (now in `url_utils`) | **Done** |
| `crm.models.Lead` | `actions/conversations.py` | Was only in `__main__` CLI block — replaced with `--urn` arg | **Done** |
| `linkedin.enums.ProfileState` | `actions/status.py`, `actions/connect.py` | Already in `linkedin/enums.py` — part of the library, not CRM | **No change needed** |

## Steps

1. ~~Extract in-place: cut all Django/DB imports from library modules, verify OpenOutreach still works~~ **Done** — `url_utils` moved out of `db/`, `VOYAGER_REQUEST_TIMEOUT_MS` moved to `api/client.py`, `crm.models` removed from actions CLI
2. Add `connect_session()` entry point — replaces `launch_browser()` + `start_browser_session()`
3. ~~Remove `_discover_and_enrich` from nav.py~~ **Already clean** — nav.py never imported it; only used `url_to_public_id` (now in `url_utils`)
4. ~~Replace `crm.models.Lead` usage in actions with plain URN/dict arguments~~ **Done** — actions already take URN/dict in public APIs; CLI `__main__` blocks updated
5. Build CLI with subcommands over existing API modules
6. Move to separate repo (`git filter-repo`)
7. Publish as `playwright-linkedin`, update OpenOutreach to `pip install` it
