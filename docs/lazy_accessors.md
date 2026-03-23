# Lazy accessors — done and candidates

## Done

### On Lead

| Accessor                 | Field         | Triggers                                      |
|--------------------------|---------------|-----------------------------------------------|
| `get_profile(session)`   | `description` | Voyager API fetch → save JSON                 |
| `get_urn(session)`       | —             | chains `get_profile` → extracts URN           |
| `get_embedding(session)` | `embedding`   | chains `get_profile` → fastembed → save bytes |

### On AccountSession

| Accessor         | Storage            | Triggers                                          |
|------------------|--------------------|---------------------------------------------------|
| `campaigns`      | `@cached_property` | DB query on first access, cached for session life  |
| `get_self_urn()` | `_self_urn`        | sentinel Lead lookup → Voyager API fallback        |

## Candidates

### AccountSession.browser / page / context

Currently lazy via `ensure_browser()` called manually before use. Already works well — callers pass `session`
around and access `.page` after ensure. Not worth changing since page is session state, not a return value.

**Location**: `linkedin/browser/session.py`

### Embedding model singleton

`_get_model()` in `linkedin/ml/embeddings.py` — global `_model = None` pattern. Works fine, could stay as-is or move to
a class if we ever need multiple models.

### Campaign kit singleton

`fetch_kit()` in `linkedin/ml/hub.py` — global `_cached_kit` with `_cache_attempted` flag. Same as above.

### BayesianQualifier.pipeline

Already lazy via `_fit_if_needed()` — the gold standard for this pattern. No changes needed.
