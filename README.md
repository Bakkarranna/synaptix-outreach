# Synaptix Studio Console

**The internal operations dashboard for Synaptix Studio's outreach engine.**

One console for the whole team — the studio lead and every operator see the
pipeline live, run the engine, read conversations, and manage who has access.
Under the hood it is the OpenOutreach engine (a self-hosted, email-first AI
sales agent): it discovers leads from a licensed data provider, qualifies them
with an ML + LLM loop, resolves work emails, and runs agentic cold outreach
from mailboxes the studio owns.

> **Distinction in Design. Discipline in Code.**

---

## Quickstart (one command each)

### Windows (the studio's daily driver)

```powershell
# First time on a machine
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

# Every time
powershell -ExecutionPolicy Bypass -File scripts\run.ps1
scripts\stop.ps1          # stop daemon + web
```

`setup.ps1` creates the virtualenv, installs dependencies, migrates the
database, bootstraps the CRM, and — if no staff account exists — walks you
through creating the **Lead** account (the person who manages everything).

`run.ps1` starts the outreach daemon and the web server in the background and
opens the browser. Open **http://localhost:8000/dashboard/** and sign in.

### Linux / macOS / WSL

```bash
bash scripts/setup.sh
bash scripts/run.sh
bash scripts/stop.sh
```

### Docker (server deployment)

```bash
cp deploy/env.example .env                      # fill in DJANGO_SECRET_KEY etc.
cp deploy/onboard_config.example.json deploy/onboard_config.json   # fill secrets
bash deploy/backup.sh                           # (after first boot) consistent snapshot
bash deploy/update.sh                           # pull + rebuild + follow the log
```

```bash
docker compose -f compose.prod.yml up -d --build
# Console: http://localhost:8000/dashboard/
```

Two services share one data volume: `web` (gunicorn serving the dashboard +
Django Admin) and `daemon` (the outreach engine). First boot provisions the
empty database **headlessly** from `deploy/onboard_config.json` (same steps and
live credential checks as the interactive wizard) — the full free-tier server
runbook, including Oracle Cloud's Always Free VM, is in **`deploy/README.md`**.
The dev image notes in `docs/docker.md` still apply to `local.yml`.

---

## Roles

| Role     | What they can do                                                                  |
|----------|-----------------------------------------------------------------------------------|
| **Lead** | Everything — configuration, team management, and the pipeline controls.           |
| **Operator** | Runs the pipeline: start/stop the daemon, nudge a cycle, read mail, pause mailboxes, suppress leads. No config, no team. |

Team members sign in at `/dashboard/` with the credentials the Lead gives them.
The **Lead** page (`/dashboard/team/`) is where members are invited, promoted,
deactivated, and given fresh passwords. There must always be at least one Lead.

---

## The dashboard

| Page       | What it does                                                                                                  |
|------------|---------------------------------------------------------------------------------------------------------------|
| **Overview** | Live funnel, headline stats (with a 14-day send sparkline), daemon controls (start / stop / nudge / read mail), mailbox capacity bars, campaign cards, recent delivery verdicts and messages. Auto-refreshes via a JSON status endpoint. |
| **Leads**    | Searchable, pageable prospect list; copy an email, open a profile, suppress/restore a lead permanently.        |
| **Deals**    | The funnel by deal state — click any row to expand the full conversation thread (what the agent wrote, what the lead replied). |
| **Live log** | The daemon's running account of its actions, auto-polling, with follow/copy controls.                          |
| **Team**     | *(Lead only)* Invite members, reset passwords, promote/demote, activate/deactivate.                            |
| **Config**   | *(Lead only)* LLM + provider keys, campaign definition (product, target, headcount, country), mailbox signature/cap/IMAP. |
| **Django Admin** | The long tail — query nodes, keywords, verdicts, message-level fixes, and the raw models. Linked from the sidebar. |

The console is a staff-only surface: nobody without a staff account can reach
any page, and every destructive control (stop daemon, suppress lead, pause
mailbox) asks for confirmation.

---

## Day-to-day operations

- **Watch the pipeline** — the Overview's funnel shows every deal by stage.
  Deals resting at *Emailed* are normal: nobody is ever chased, the agent only
  writes when the lead writes back.
- **Start / stop the engine** — the daemon is a detached process tracked by
  pidfile (`data/daemon.pid`). The Overview card starts, stops and nudges it;
  `scripts\stop.ps1` stops daemon + web together. Stopping is always safe —
  every piece of pipeline state lives in the DB, so a restart resumes exactly
  where it left off.
- **Back up** — the whole store is one SQLite file: `data/db.sqlite3`. Copy it
  (ideally with the daemon stopped) to back up leads, deals and conversations.
  There is also `python manage.py reset_data` to wipe pipeline data for a fresh
  run (it keeps a timestamped backup first).
- **Mailboxes** — each mailbox has a measured daily cap and its own pacing;
  the Overview shows headroom live. Replies are read from the IMAP inbox
  configured per mailbox (the studio uses Hostinger SMTP + Gmail IMAP with
  Cloudflare routing for `outreach@synaptixstudio.com`).

---

## Packaging & distribution

`scripts\package.ps1` (or `scripts\package.sh`) builds a self-contained bundle
in `dist/` — the app minus virtualenv, git history, and data. Hand it to a new
machine or a server and run `setup` → `run`. The recipient starts with an
empty database and onboards through the wizard (see below).

---

## Onboarding (first run)

The engine needs five things before it sends anything:

1. **Campaign** — product description, target market, booking link.
2. **LLM** — an OpenAI-compatible endpoint (the studio uses NVIDIA NIM).
3. **Mailbox** — an SMTP sending identity + IMAP reply inbox.
4. **BetterContact** — the API key powering discovery *and* email resolution.
5. **Account** — the operator's email, country, newsletter, legal acceptance.

The interactive wizard runs on the daemon's first start (TTY) and is fully
resumable. For a non-interactive install, `onboard.py` (in the repo root of the
development workspace) replays `onboard_config.json` against the DB — see
`docs/configuration.md`.

---

## Layout

```
scripts/                  setup / run / stop / package (PowerShell + bash)
openoutreach/             the engine + console
  dashboard/              the Synaptix Studio Console (views, templates)
  core/                   campaign, SiteConfig, LLM factory, ML + discovery pipeline
  emails/                 enrichment, Mailbox + SMTP/IMAP, sender, pipeline steps
  crm/                    Lead + Deal models (the funnel)
  chat/                   ChatMessage — the per-deal conversation
compose.prod.yml          production stack (web + daemon, shared volume)
local.yml                 docker dev container
docs/                     architecture, configuration, docker, templating, testing
```

## Attribution & legal

The Console is built on **OpenOutreach** (GPLv3) — the browserless, email-first
AI sales agent that powers discovery, qualification and sending. Using the
software means accepting the [Legal Notice](LEGAL_NOTICE.md): it covers the
third-party services you connect (data provider, email-finder, mailbox), your
responsibilities as data controller and sender under data-protection and
anti-spam law, and liability disclaimers.

**Use at your own risk — no liability assumed.**
