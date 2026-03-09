# linkedin/pipeline/pools.py
"""Pool management and backfill orchestration.

Stateless functions for querying the ready-to-connect pool and
orchestrating the qualify→search backfill chain when the pool is empty.
"""
from __future__ import annotations

import logging

from linkedin.conf import CAMPAIGN_CONFIG
from linkedin.db.crm_profiles import get_qualified_profiles
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.pipeline.qualify import qualify_one
from linkedin.pipeline.ready_pool import get_ready_candidate, promote_to_ready
from linkedin.pipeline.search import search_one

logger = logging.getLogger(__name__)


def get_candidate(session, qualifier: BayesianQualifier, pipeline=None, is_partner: bool = False) -> dict | None:
    """Top profile ready for connection, backfilling if needed.

    Partner campaigns bypass READY_TO_CONNECT and pick directly from the NEW pool.
    Regular campaigns require profiles to pass the GP confidence gate first.
    """
    if is_partner:
        profiles = get_qualified_profiles(session)
        if not profiles:
            return None
        ranked = qualifier.rank_profiles(profiles, session=session, pipeline=pipeline)
        return ranked[0] if ranked else None

    threshold = CAMPAIGN_CONFIG["min_ready_to_connect_prob"]

    while True:
        candidate = get_ready_candidate(session, qualifier, pipeline=pipeline)
        if candidate is not None:
            return candidate

        promoted = promote_to_ready(session, qualifier, threshold)
        if promoted > 0:
            continue

        if qualify_one(session, qualifier) is not None:
            continue
        if search_one(session) is not None:
            continue
        return None
