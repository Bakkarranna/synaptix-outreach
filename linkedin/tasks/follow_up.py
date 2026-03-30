# linkedin/tasks/follow_up.py
"""Follow-up task — runs the agentic follow-up for one CONNECTED profile."""
from __future__ import annotations

import logging

from termcolor import colored

from linkedin.db.deals import get_profile_dict_for_public_id
from linkedin.models import ActionLog

logger = logging.getLogger(__name__)


def handle_follow_up(task, session, qualifiers):
    from linkedin.actions.message import send_raw_message
    from linkedin.agents.follow_up import run_follow_up_agent
    from linkedin.db.deals import set_profile_state
    from linkedin.enums import ProfileState
    from linkedin.tasks.connect import enqueue_follow_up

    payload = task.payload
    public_id = payload["public_id"]
    campaign_id = payload["campaign_id"]

    logger.info(
        "[%s] %s %s",
        session.campaign, colored("\u25b6 follow_up", "green", attrs=["bold"]), public_id,
    )

    # Rate limit check
    if not session.linkedin_profile.can_execute(ActionLog.ActionType.FOLLOW_UP):
        enqueue_follow_up(campaign_id, public_id, delay_seconds=3600)
        return

    profile_dict = get_profile_dict_for_public_id(session, public_id)
    if profile_dict is None:
        logger.warning("follow_up: no Deal for %s — skipping", public_id)
        return

    profile = profile_dict.get("profile") or profile_dict

    decision = run_follow_up_agent(session, public_id, profile)

    if decision.action == "send_message":
        sent = send_raw_message(session, profile, decision.message)
        if not sent:
            set_profile_state(session, public_id, ProfileState.QUALIFIED.value)
            logger.warning("follow_up for %s: send failed — moving to QUALIFIED for re-connection", public_id)
            return
        session.linkedin_profile.record_action(
            ActionLog.ActionType.FOLLOW_UP, session.campaign,
        )
        enqueue_follow_up(campaign_id, public_id, delay_seconds=decision.follow_up_hours * 3600)

    elif decision.action == "mark_completed":
        set_profile_state(session, public_id, ProfileState.COMPLETED.value, reason=decision.reason)

    elif decision.action == "wait":
        enqueue_follow_up(campaign_id, public_id, delay_seconds=decision.follow_up_hours * 3600)
