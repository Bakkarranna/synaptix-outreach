# Profile Lifecycle

## Overview

Every LinkedIn profile flows through a fixed sequence of stages, from first
discovery on a page to the final follow-up message.

```
Discovery → Enrichment + Embedding → Qualification (LLM) → NEW → READY_TO_CONNECT (GP gate) → PENDING → CONNECTED → COMPLETED
  (url)       (voyager + fastembed)     (always LLM)         (Deal)     (GP prob > threshold)     (sent)    (accepted)   (followed up)
```

---

## 1. Discovery

**Where:** `navigation/utils.py` — `goto_page()` → `_extract_in_urls()` → `_enrich_new_urls()`

Every time the daemon navigates to a LinkedIn page (search results, profile
pages, feed), all `/in/` URLs on the page are extracted. New URLs (those
without an existing Lead) are immediately processed.

LLM-generated search keywords (`pipeline/search.py:search_one()`) drive
additional discovery when the candidate pool runs dry.

## 2. Enrichment + Embedding (eager, at discovery time)

**Where:** `navigation/utils.py:_enrich_new_urls()` → `db/crm_profiles.py:create_enriched_lead()` → `ml/embeddings.py:embed_profile()`

For each new URL discovered:

1. **Voyager API** fetches structured profile data (name, headline, positions, education, etc.)
2. **Lead** is created with the full profile JSON in `description`
3. **Company** is created/linked from the first position
4. **ProfileEmbedding** is computed (384-dim BAAI/bge-small-en-v1.5 via fastembed) and stored with `label=null`

All three steps happen atomically at discovery time. Rate-limited by
`enrich_min_interval` (default 1s per profile).

> **Robustness fallback:** Lazy helpers (`ensure_lead_enriched`,
> `ensure_profile_embedded`) exist in `db/crm_profiles.py` for rare edge
> cases (manual lead creation, interrupted enrichment, DB inconsistency).
> They log a warning when triggered — this is not normal flow.

## 3. Qualification (LLM only)

**Where:** `pipeline/qualify.py:qualify_one()` (called from connect lane backfill)

Unlabeled `ProfileEmbedding` rows are the qualification pool. Candidate
selection depends on label balance:

| Condition | Strategy | Method |
|-----------|----------|--------|
| `n_negatives > n_positives` | **Exploit** — pick highest predicted probability | `qualifier.predict_probs()` |
| Otherwise | **Explore** — pick highest BALD score | `qualifier.compute_bald()` |

All qualification decisions go through the LLM via `qualify_lead.j2` prompt.
The GP model is used only for candidate selection strategy, not for auto-decisions.

### Cold start

With fewer than 2 labels or a single class, the GP model returns `None`.
The first candidate is selected in order and qualified via LLM.

### Result

- `ProfileEmbedding.label` set to 0 or 1, with `llm_reason` and `labeled_at`
- Accepted: Lead promoted → Contact + Company + Deal (stage=NEW)
- Rejected: `Lead.disqualified = True`

## 4. Ready to Connect (NEW → READY_TO_CONNECT)

**Where:** `pipeline/ready_pool.py:promote_to_ready()`

After qualification, profiles sit at the NEW stage. Before connecting, they
must pass a GP confidence gate:

- `promote_to_ready()` loads all NEW profiles, computes P(f > 0.5) via the GP model
- Profiles with probability above `min_ready_to_connect_prob` (default 0.9) are promoted to READY_TO_CONNECT
- During cold start (GP not fitted), no profiles are promoted — the connect lane keeps triggering qualifications until enough labels accumulate

## 5. Connect (READY_TO_CONNECT → PENDING)

**Where:** `lanes/connect.py`

The connect lane picks the top READY_TO_CONNECT profile from the pool
(`pipeline/pools.py:get_candidate()` → `pipeline/ready_pool.py:get_ready_candidate()`).

If the pool is empty, the **backfill chain** runs:
1. `promote_to_ready()` — check if any NEW profiles pass the GP gate
2. `qualify_one()` — qualify the next unlabeled profile via LLM
3. `search_one()` — discover new profiles via LinkedIn search
4. Re-check pool — repeat until a candidate is found or all return None

Connection request is sent without a note. Deal moves to PENDING stage.
Rate-limited by `LinkedInProfile.can_execute()` / `record_action()`.

## 6. Check Pending (PENDING → CONNECTED)

**Where:** `lanes/check_pending.py`

Polls all PENDING profiles for acceptance. Uses **exponential backoff**
per profile:

- Initial interval: `check_pending_recheck_after_hours` (default 24h)
- Doubles each time the profile is still pending
- Stored in `deal.next_step` as `{"backoff_hours": N}`

All ready profiles are checked per tick. On acceptance → CONNECTED.

## 7. Follow Up (CONNECTED → COMPLETED)

**Where:** `lanes/follow_up.py`

Sends a follow-up message to one CONNECTED profile per tick:

1. Renders Jinja2 template (`followup2.j2`) with profile context
2. Passes through LLM for natural language refinement
3. Appends booking link (after LLM call, not part of prompt)
4. Sends via LinkedIn messaging

Deal moves to COMPLETED stage. Message persisted via `save_chat_message()`.

## 8. Terminal States

- **COMPLETED** — follow-up message sent successfully
- **FAILED** — unrecoverable error at any stage

---

## State Diagram

```
                    ┌─────────────┐
                    │  Discovered │  Lead created (url-only or enriched)
                    │  (implicit) │  ProfileEmbedding created (label=null)
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ Qualification│  LLM query (always)
                    └──┬───────┬──┘
                       │       │
              rejected │       │ accepted
                       │       │
            ┌──────────▼┐  ┌───▼──────┐
            │Disqualified│  │   NEW    │  Contact + Deal created
            │ (implicit) │  └────┬─────┘
            └────────────┘       │
                                 │ GP confidence gate (P(f>0.5) > threshold)
                          ┌──────▼──────────────┐
                          │  READY_TO_CONNECT   │  GP model confident
                          └──────┬──────────────┘
                                 │ send_connection_request()
                          ┌──────▼──────┐
                          │   PENDING   │  Waiting for acceptance
                          └──────┬──────┘
                                 │ connection accepted
                          ┌──────▼──────┐
                          │  CONNECTED  │  Ready for follow-up
                          └──────┬──────┘
                                 │ send_follow_up_message()
                          ┌──────▼──────┐
                          │  COMPLETED  │  Done
                          └─────────────┘

                          ┌─────────────┐
                          │   FAILED    │  Error at any stage
                          └─────────────┘
```

## Partner Campaigns

Partner campaigns skip qualification, READY_TO_CONNECT, and search entirely.
They re-use disqualified leads from other campaigns, seeded via
`seed_partner_deals()`. Profiles go straight from NEW pool to connect,
gated by `action_fraction`.
