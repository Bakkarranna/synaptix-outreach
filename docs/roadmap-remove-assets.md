# Roadmap: Eliminate the `assets/` Bind Mount

## Problem

The Docker quick-start requires `git clone` before `docker run` just to ensure the `assets/` directory exists with correct ownership. If a user runs the container without cloning first, Docker creates `assets/` as root, and the non-root container user gets `PermissionError`.

## Goal

Run the pre-built Docker image with **zero prerequisites** — no git clone, no directory setup:

```bash
docker run --pull always -it -p 5900:5900 ghcr.io/eracle/openoutreach:latest
```

All persistent state lives inside the SQLite database (already a Docker volume), eliminating the need for a structured `assets/` bind mount.

## What Currently Lives in `assets/`

| Path | Contents | Migration Strategy |
|------|----------|--------------------|
| `data/crm.db` | SQLite database | Keep as a named Docker volume (`-v openoutreach-data:/app/assets/data`) |
| `data/media/docs/` | Raw Voyager JSON files (previously attached via TheFile) | TheFile model removed — raw data no longer persisted |
| `cookies/*.json` | Playwright browser cookies | Store as `BinaryField` on `LinkedInProfile` model |
| `cookies/.legal_notice_accepted` | Legal acceptance marker | Add `legal_accepted` `BooleanField` to `LinkedInProfile` |
| `cookies/.*_newsletter_processed` | GDPR newsletter marker | Add `newsletter_processed` `BooleanField` to `LinkedInProfile` |
| `models/campaign_*_model.joblib` | Serialized sklearn GP models | Store as `BinaryField` on `Campaign` model |
| `models/hub/` | Downloaded partner campaign kit | Store as `BinaryField` on `Campaign` model (or re-download on startup) |
| `templates/prompts/*.j2` | Jinja2 prompt templates | Already shipped in the Docker image — no bind mount needed |
| `diagnostics/` | Failure screenshots/HTML/tracebacks | Store in DB or make ephemeral (container-local tmpdir) |
| `.env` | `LLM_API_KEY`, `AI_MODEL`, `LLM_API_BASE` | Pass as `docker run -e` env vars (already supported by `os.getenv`) |
| `campaign/*.txt` | Legacy campaign text files | Already migrated to DB (`Campaign` model fields) — delete |
| `accounts.secrets.yaml` | Legacy credentials file | Already migrated to DB (`LinkedInProfile`) — delete |

## Implementation Phases

### Phase 1: Named Volume for SQLite (Quick Win)

Replace the bind mount with a named Docker volume for just the database:

```bash
docker run -it -p 5900:5900 -v openoutreach-data:/app/assets/data ghcr.io/eracle/openoutreach:latest
```

- No `git clone` needed, no ownership issues (Docker manages named volumes)
- Other `assets/` subdirs become container-local (ephemeral but functional)
- Cookies/models are lost on container recreation — acceptable for MVP, fixed in Phase 2

### Phase 2: Cookies and Markers to DB

- Add `cookie_data` (`BinaryField`, nullable) to `LinkedInProfile` — stores the Playwright cookie JSON
- Add `legal_accepted` (`BooleanField`, default=False) to `LinkedInProfile`
- Add `newsletter_processed` (`BooleanField`, default=False) to `LinkedInProfile`
- Update `browser/login.py` to load/save cookies from the model instead of disk
- Update `onboarding.py` and `setup/gdpr.py` to check DB fields instead of marker files
- Migration: on startup, if cookie file exists but DB field is empty, import from file

### Phase 3: ML Models to DB

- Add `model_blob` (`BinaryField`, nullable) to `Campaign` — stores the serialized joblib bytes
- Update `ml/qualifier.py` save/load to use `Campaign.model_blob` instead of disk files
- Update `ml/hub.py` to store downloaded kit model in `Campaign.model_blob`
- Remove `MODELS_DIR`, `model_path_for_campaign()`, `_LEGACY_MODEL_PATH` from `conf.py`
- Migration: on startup, if model file exists but DB field is empty, import from file

### Phase 4: Diagnostics to DB or Tmpdir

- Option A: Add a `Diagnostic` model (screenshot as `BinaryField`, HTML as `TextField`, traceback as `TextField`, timestamp, FK to Task)
- Option B: Write to `/tmp/openoutreach-diagnostics/` (container-local, survives restarts with `--restart` but not recreation)
- Option B is simpler and diagnostics are rarely needed after the fact

### Phase 5: Remove `assets/` Entirely

- Remove all `mkdir` calls from `conf.py` and `django_settings.py`
- Remove `ASSETS_DIR`, `COOKIES_DIR`, `MODELS_DIR`, `DIAGNOSTICS_DIR` path constants
- Update `DATA_DIR` to point to `/app/data` (or keep `assets/data` for backwards compat)
- Docker command becomes just: `docker run -it -p 5900:5900 -v openoutreach-data:/app/data ghcr.io/eracle/openoutreach:latest`
- Delete legacy files: `assets/campaign/`, `assets/accounts.secrets.yaml`
- Update `.gitignore`, README, docker.md, CLAUDE.md

## Backwards Compatibility

Each phase should include a one-time migration that imports existing file-based data into the DB on startup, so existing users don't lose state when upgrading.
