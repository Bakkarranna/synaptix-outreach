# openoutreach/core/pipeline/ready_pool.py
"""Find-email pool: GP confidence gate between QUALIFIED and READY_TO_FIND_EMAIL.

The rank gate for the paid action. It promotes only QUALIFIED leads scoring at or
above ``CAMPAIGN_CONFIG["min_gp_confidence"]`` into READY_TO_FIND_EMAIL, so a
BetterContact credit is only ever spent on a ranked lead. ``pools._advance`` reads
the same constant in its exploit branch — a lead below it would be qualified and
then parked here, so it is only worth an LLM call for the label (which exploit does
not want; that is the explore branch's job).

That constant is the **spend gate and nothing else**. Discovery once borrowed it to
score query nodes ("how many of this query's leads would we pay for?"). That was a
category error: calibrated against *labelled* leads the GP has memorized, the bar is
unreachable for the unlabelled candidates a query is made of, so every node scored
zero and discovery read a permanent wall. Discovery now scores queries with the same
GP that ranks leads, on keyword embeddings — never this gate. Keep it that way — see
``pipeline/select.py`` and the roadmap card
``p2-e3-discovery-unified-gp-query-selection``.
"""
from __future__ import annotations

import logging

import numpy as np
from termcolor import colored

from openoutreach.core.conf import CAMPAIGN_CONFIG
from openoutreach.core.db.deals import (
    get_qualified_profiles,
    get_ready_to_find_email_profiles,
    set_profile_state,
)
from openoutreach.core.ml.qualifier import Qualifier
from openoutreach.crm.models import DealState

logger = logging.getLogger(__name__)


def promote_to_ready(campaign, qualifier: Qualifier) -> int:
    """Promote QUALIFIED profiles at or above the GP confidence gate to
    READY_TO_FIND_EMAIL.

    The gate is ``CAMPAIGN_CONFIG["min_gp_confidence"]`` — read here rather than passed
    so it cannot drift from the copy ``pools._advance`` uses to decide what is worth an
    exploit qualification. Returns the number of profiles promoted; 0 when the GP model
    is not fitted (cold start) or when no QUALIFIED profiles exist.
    """
    from openoutreach.crm.models import Lead

    threshold = CAMPAIGN_CONFIG["min_gp_confidence"]
    profiles = get_qualified_profiles(campaign)
    if not profiles:
        return 0

    embeddings = []
    valid = []
    for p in profiles:
        lead = Lead.objects.filter(pk=p.get("lead_id")).first()
        emb = lead.embedding_array if lead else None
        if emb is not None:
            embeddings.append(emb)
            valid.append(p)

    if not valid:
        return 0

    X = np.array(embeddings, dtype=np.float64)
    stats = qualifier.predict_stats(X)
    if stats is None:
        return 0
    probs, means, stds = stats

    # Persist the score on every scored deal, promoted or not — the operator's
    # window onto why a lead passed or failed the gate. One update per row rather
    # than a Case/When bulk write: the pool is tens of rows and this runs once per
    # scoring pass, so clarity beats cleverness. The score never rides in `reason`
    # — that column holds the LLM's qualification rationale, and overwriting it
    # with a probability would destroy why the lead qualified.
    from openoutreach.crm.models import Deal, GpScoreRecord

    # One history row per scored deal per pass — the record of how the model's
    # confidence in this lead evolved across refits while it waited in the pool,
    # carrying the GP mean/std behind each P so a borderline score is visibly
    # borderline. Written before the snapshot so a crash can never leave a newer
    # snapshot without its history entry; bulk_create keeps the whole pass at one
    # query.
    #
    # The profile dicts carry ``lead_id`` (the LEAD's pk — ``to_profile_dict``),
    # not the Deal's, so the Deal pk is resolved in one query before writing.
    # Writing the lead pk into the Deal FK would violate it the moment the two
    # sequences drift (in production they always have — a live install surfaced
    # the ``FOREIGN KEY constraint failed`` on the very first scoring pass).
    deal_ids = dict(
        Deal.objects.filter(campaign=campaign, lead_id__in=[p["lead_id"] for p in valid])
        .values_list("lead_id", "pk")
    )
    GpScoreRecord.objects.bulk_create([
        GpScoreRecord(
            deal_id=deal_ids[p["lead_id"]],
            score=float(prob),
            mean=float(mean),
            std=float(std),
        )
        for prob, mean, std, p in zip(probs, means, stds, valid)
    ])

    for prob, p in zip(probs, valid):
        Deal.objects.filter(campaign=campaign, lead_id=p.get("lead_id")).update(
            gp_score=float(prob))

    promoted = 0
    for prob, p in zip(probs, valid):
        if prob >= threshold:
            pid = p.get("profile_url", "?")
            # One line, carrying the score that justified the promotion. `log=False`
            # because this *is* the spine line — `set_profile_state` would otherwise
            # print the same transition again without the number.
            logger.info("%s %s (P(f>0.5)=%.3f ≥ %.2f)", pid,
                        colored("READY_TO_FIND_EMAIL", "yellow", attrs=["bold"]),
                        prob, threshold)
            set_profile_state(campaign, p["profile_url"],
                              DealState.READY_TO_FIND_EMAIL.value, log=False)
            promoted += 1

    return promoted


def find_ready_candidate(campaign, qualifier: Qualifier) -> dict | None:
    """Return the top-ranked READY_TO_FIND_EMAIL profile, or None."""
    profiles = get_ready_to_find_email_profiles(campaign)
    if not profiles:
        return None

    ranked = qualifier.rank_profiles(profiles)
    return ranked[0] if ranked else None
