# Partner Campaign Simplification Roadmap

Tracking remaining items to reduce partner-related cyclomatic complexity.

## Done

- [x] **Split connect task types** — Dedicated `connect_partner` task type with its own handler in
  `tasks/connect_partner.py`. Removed probabilistic gating; `action_fraction` now controls reschedule delay (
  `base_delay / fraction`), giving deterministic 1:N ratio between partner and regular connects.

- [x] **~~Clean up partner logging boilerplate~~** — All task handlers now use `[{campaign_name}]` prefix with
  `logging.INFO`. No more `PARTNER_LOG_LEVEL` / `is_partner` checks in task handlers.

- [x] **Eliminate `seed_partner_deals` + remove `is_partner` from CRM queries** (items 2 + 4) — Partner candidates are
  now selected directly from `ProfileEmbedding` via `get_partner_candidate()` in `pipeline/partner_pool.py`. A Deal is
  created just-in-time via `create_partner_deal()` only for the selected candidate, not bulk-created upfront. This
  eliminated the O(n) scan per iteration, removed all `is_partner` branches from `get_qualified_profiles`,
  `count_qualified_profiles`, and `get_ready_to_connect_profiles`, and removed the `threshold <= 0` shortcut from
  `promote_to_ready`. Partner campaigns no longer flow through the Deal-based pool system (`pools.py` / `ready_pool.py`)
  at all.

## Next Steps

### 1. Extract `_do_connect` shared logic

Both `handle_connect` and `handle_connect_partner` share the same core flow (rate check, get candidate, connection
status check, send request, enqueue follow-ons). Extract into a shared helper to eliminate duplication. The two handlers
become thin wrappers that set qualifier/pipeline/delay/tag and delegate.

### 2. Unify the qualifier/pipeline interface

The `partner_qualifier` + `kit_model` pair is threaded through the daemon and handler signatures:
`run_daemon → handler → get_partner_candidate → rank_profiles`. Instead, each campaign could carry its own qualifier
object (partner campaigns wrap `kit_model` in a qualifier-compatible adapter). This eliminates the separate
`partner_qualifier` arg from all handler signatures.
