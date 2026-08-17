from django import template

from openoutreach.crm.models import DealState

register = template.Library()

_PILL = {
    DealState.QUALIFIED: "qual",
    DealState.READY_TO_FIND_EMAIL: "ready-find",
    DealState.FINDING_EMAIL: "finding",
    DealState.READY_TO_EMAIL: "ready-mail",
    DealState.EMAILED: "emailed",
    DealState.NO_EMAIL_BETTERCONTACT: "no-email",
    DealState.UNSUBSCRIBED: "unsub",
    DealState.COMPLETED: "completed",
    DealState.FAILED: "failed",
}


@register.filter
def pillclass(state):
    """DealState value → the CSS class of its pill (stable across renames)."""
    return _PILL.get(state, "unsub")
