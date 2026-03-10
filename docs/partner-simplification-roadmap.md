# Partner Campaign Simplification Roadmap

Tracking remaining items to reduce partner-related cyclomatic complexity.

## Done

- [x] **Split connect task types** — Dedicated `connect_partner` task type with its own handler in `tasks/connect_partner.py`. Removed probabilistic gating; `action_fraction` now controls reschedule delay (`base_delay / fraction`), giving deterministic 1:N ratio between partner and regular connects.

## Next Steps

### 1. Extract `_do_connect` shared logic
Both `handle_connect` and `handle_connect_partner` share the same core flow (rate check, get candidate, connection status check, send request, enqueue follow-ons). Extract into a shared helper to eliminate duplication. The two handlers become thin wrappers that set qualifier/pipeline/delay/tag and delegate.

### 2. Move `seed_partner_deals` to a periodic task or startup
Currently called every `handle_connect_partner` iteration — O(n) scan of all disqualified leads each time. Should run once at startup (in `heal_tasks`) and then periodically (e.g. new `seed_partner` task type that reschedules every hour), not on every connect.

### 3. Unify the qualifier/pipeline interface
The `partner_qualifier` + `kit_model` pair is threaded through 6 layers: `run_daemon → handler → get_candidate → ready_source → get_ready_candidate → rank_profiles`. Instead, each campaign could carry its own qualifier object (partner campaigns wrap `kit_model` in a qualifier-compatible adapter). This eliminates the separate `partner_qualifier` arg from all handler signatures.

### 4. Remove `is_partner` checks from CRM query layer
Three functions in `crm_profiles.py` (`get_qualified_profiles`, `count_qualified_profiles`, `get_ready_to_connect_profiles`) have identical `if not is_partner: filter(disqualified=False)` branches. Instead, partner campaigns should pre-filter at the deal level (the `seed_partner_deals` function already creates deals only from disqualified+embedded leads). Once seeded, the CRM queries don't need to know about partner status — they just query deals in the campaign's department.

### 5. Clean up partner logging boilerplate
`check_pending.py` and `follow_up.py` both have identical 3-line blocks:
```python
is_partner = getattr(session.campaign, "is_partner", False)
log_level = PARTNER_LOG_LEVEL if is_partner else logging.INFO
tag = "[Partner] " if is_partner else ""
```
This could be a simple utility (e.g. `campaign_log_prefix(campaign)`) or the campaign model could expose a `log_level` property.

### 6. Consider separate `check_pending_partner` / `follow_up_partner` task types
Currently these are shared between partner and regular campaigns (only logging differs). If partner campaigns need different behavior in the future (e.g. different backoff, different message templates), having separate task types would be cleaner. Low priority since the current divergence is minimal.
