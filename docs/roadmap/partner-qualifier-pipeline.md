# Partner qualifier pipeline shows wrong model stats

## Problem

Partner connect logs show the inner `BayesianQualifier`'s predictions instead of the kit model's:

```
[Partner Outreach] jaycie-poitra (prob=0.000, entropy=0.0002, std=0.1458, obs=169)
```

The `prob` here comes from the inner BayesianQualifier (trained on all labels), not the kit model that actually ranks candidates.

## Why it happens

1. `fetch_kit()` can return `None` (download fails, cache miss, HuggingFace down). When it does, `_build_qualifiers` creates `KitQualifier(kit_model=None, inner=...)`.

2. `KitQualifier.explain` delegates to `self._inner.explain(profile, session, pipeline=self._kit_model)`. When `_kit_model` is `None`, `explain` treats `pipeline=None` as "use internal model" — the same sentinel value means both "not provided" and "missing kit".

3. The `pipeline=None` ambiguity exists in three places: `explain`, `rank_profiles`, and `_get_pipeline`. All silently fall back to the inner model when the kit is `None`.

4. `heal_tasks` seeds connect tasks for all campaigns unconditionally, so partner campaigns without a qualifier still get tasks queued — they fail at execution time, not at startup.

## Where it lives

- `ml/qualifier.py` — `KitQualifier`, `BayesianQualifier.explain`, `_predict_from`, `_get_pipeline`
- `daemon.py` — `_build_qualifiers`, `heal_tasks`
- `tasks/connect.py` — `strategy_for`
