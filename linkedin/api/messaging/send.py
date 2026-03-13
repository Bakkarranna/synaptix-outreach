# linkedin/api/messaging/send.py
"""Send messages via Voyager Messaging API."""
import base64
import json
import logging
import os
import uuid
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from linkedin.api.client import PlaywrightLinkedinAPI, REQUEST_TIMEOUT_MS
from linkedin.api.messaging.utils import get_self_urn, check_response

logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(IOError),
    reraise=True,
)
def send_message(
        api: PlaywrightLinkedinAPI,
        conversation_urn: str,
        message_text: str,
        mailbox_urn: Optional[str] = None,
) -> dict:
    """Send a message via Voyager Messaging API.

    Args:
        api: Authenticated PlaywrightLinkedinAPI instance.
        conversation_urn: e.g. "urn:li:msg_conversation:(urn:li:fsd_profile:XXX,2-threadId)"
        message_text: The message body.
        mailbox_urn: Sender's profile URN. Auto-discovered from /in/me/ if omitted.

    Returns:
        API response dict with delivery confirmation.
    """
    if not mailbox_urn:
        mailbox_urn = get_self_urn(api)

    origin_token = str(uuid.uuid4())
    tracking_id = base64.b64encode(os.urandom(16)).decode("ascii")

    payload = {
        "message": {
            "body": {
                "attributes": [],
                "text": message_text,
            },
            "renderContentUnions": [],
            "conversationUrn": conversation_urn,
            "originToken": origin_token,
        },
        "mailboxUrn": mailbox_urn,
        "trackingId": tracking_id,
        "dedupeByClientGeneratedToken": False,
    }

    url = (
        "https://www.linkedin.com/voyager/api"
        "/voyagerMessagingDashMessengerMessages?action=createMessage"
    )

    headers = {**api.headers}
    headers["accept"] = "application/json"
    headers["content-type"] = "text/plain;charset=UTF-8"

    logger.debug("Voyager send_message → %s", conversation_urn)

    res = api.context.request.post(
        url, data=json.dumps(payload), headers=headers,
        timeout=REQUEST_TIMEOUT_MS,
    )
    check_response(res, "send_message")

    data = res.json()
    delivered_at = data.get("value", {}).get("deliveredAt")
    logger.info("Message delivered → %s (at %s)", conversation_urn, delivered_at)
    return data
