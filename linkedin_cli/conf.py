"""Platform constants for the LinkedIn interaction layer.

Browser timing/launch knobs and fixture paths — no campaign, CRM, or
scheduling config (that stays in OpenOutreach's ``linkedin/conf.py``).
"""
from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent

# ----------------------------------------------------------------------
# Fixture paths (saved HTML pages + profile JSON for tests/fixture capture)
# ----------------------------------------------------------------------
FIXTURE_DIR = ROOT_DIR / "tests" / "fixtures"
FIXTURE_PROFILES_DIR = FIXTURE_DIR / "profiles"
FIXTURE_PAGES_DIR = FIXTURE_DIR / "pages"
DUMP_PAGES = False

# ----------------------------------------------------------------------
# Browser config
# ----------------------------------------------------------------------
BROWSER_SLOW_MO = 200
BROWSER_DEFAULT_TIMEOUT_MS = 30_000
BROWSER_LOGIN_TIMEOUT_MS = 40_000
BROWSER_NAV_TIMEOUT_MS = 10_000
HUMAN_TYPE_MIN_DELAY_MS = 50
HUMAN_TYPE_MAX_DELAY_MS = 200

# Seconds to wait for the user to clear a LinkedIn security checkpoint in the
# live browser (noVNC http://localhost:6080/vnc.html) before the daemon exits.
CHECKPOINT_RESOLVE_TIMEOUT_S = 1800
