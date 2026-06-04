# linkedin/api/messaging/conversations.py
"""Retrieve conversations and messages via Voyager Messaging GraphQL API."""
import logging

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from linkedin_cli.api.client import PlaywrightLinkedinAPI
from linkedin_cli.api.messaging.utils import encode_urn, check_response

logger = logging.getLogger(__name__)

_GRAPHQL_BASE = "https://www.linkedin.com/voyager/api/voyagerMessagingGraphQL/graphql"
_CONVERSATIONS_QUERY_ID = "messengerConversations.0d5e6781bbee71c3e51c8843c6519f48"
_MESSAGES_QUERY_ID = "messengerMessages.5846eeb71c981f11e0134cb6626cc314"


def _graphql_headers(api: PlaywrightLinkedinAPI) -> dict:
    headers = {**api.headers}
    headers["accept"] = "application/graphql"
    return headers


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(IOError),
    reraise=True,
)
def fetch_conversations(api: PlaywrightLinkedinAPI, mailbox_urn: str) -> dict:
    """Fetch recent conversations list. Returns raw API response."""
    url = (
        f"{_GRAPHQL_BASE}"
        f"?queryId={_CONVERSATIONS_QUERY_ID}"
        f"&variables=(mailboxUrn:{encode_urn(mailbox_urn)})"
    )
    res = api.get(url, headers=_graphql_headers(api))
    check_response(res, "fetch_conversations")
    return res.json()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(IOError),
    reraise=True,
)
def fetch_messages(api: PlaywrightLinkedinAPI, conversation_urn: str) -> dict:
    """Fetch messages for a conversation. Returns raw API response."""
    url = (
        f"{_GRAPHQL_BASE}"
        f"?queryId={_MESSAGES_QUERY_ID}"
        f"&variables=(conversationUrn:{encode_urn(conversation_urn)})"
    )
    res = api.get(url, headers=_graphql_headers(api))
    check_response(res, "fetch_messages")
    return res.json()
