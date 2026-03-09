# Roadmap: ProfileState Enum Cleanup

## Problem

`ProfileState` enum values (`NEW`, `PENDING`, etc.) don't match the CRM stage names they map to. For example, `ProfileState.NEW` maps to the "Qualified" stage via `STATE_TO_STAGE`. This indirection adds confusion without clear benefit — the enum is essentially a second naming layer on top of the stage names.

## Proposed Solution

Remove `ProfileState` enum entirely. Use CRM stage name strings directly throughout the codebase:

- `get_connection_status()` / `send_connection_request()` return stage name strings instead of enum members
- `set_profile_state()` takes stage name strings directly, eliminating the `STATE_TO_STAGE` mapping
- Lane logic compares against stage name strings (e.g., `"Pending"`, `"Connected"`)

## Trade-offs

- **Pro:** Single source of truth — stage names defined once in `setup_crm.py`, used everywhere
- **Pro:** No confusing mismatch between enum names and DB values
- **Con:** Lose type safety from enum (typos in strings won't be caught statically)
- **Con:** Touches many files (actions, lanes, pools, crm_profiles, tests)
