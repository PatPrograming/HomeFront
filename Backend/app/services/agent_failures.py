"""What the staffer reads when the chat agent can't answer.

The agent says why in the chat itself whenever it still holds a mandate token (agent/mandate_agent/chat.py).
When it can't (no identity, no broker), it exits with a code, and the supervisor posts the matching
message here. The staffer gets plain words; a short technical detail is stored beside the message and
shown only to admins. Never an exception's text, a secret or a traceback.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from identity_gateway.gateway import get_gateway
from mandate_contracts.ddb import ChatMessageItem

log = logging.getLogger("homefront.agent_supervisor")

# Mirrors agent/mandate_agent/chat.py and runner.py (a test keeps them equal).
EXIT_CODES = {"EXIT_OK": 0, "EXIT_STOPPED": 3, "EXIT_NOT_SET_UP": 4, "EXIT_UNREACHABLE": 5, "EXIT_FAILED": 6,
              "EXIT_IDENTITY_REFUSED": 7, "EXIT_UNEXPLAINED": 8}
SAID_ALREADY = {0, 3, 6}  # finished, session over, or the agent posted the reason itself

NOT_SET_UP = "The assistant isn't set up on this computer (agent secret missing). Ask your admin."
IDENTITY_REFUSED = "The assistant couldn't sign in: its credentials were refused. Ask your admin."
UNREACHABLE = ("The assistant couldn't reach its sign-in service, so it can't answer right now. "
               "Send your message again in a minute, or ask your admin.")
CRASHED = "The assistant stopped unexpectedly. Send your message again to restart it."
UNEXPLAINED = ("The assistant stopped because of a problem on the server and couldn't say why. "
               "Send your message again, or ask your admin.")
STAFF_WORDS = {4: NOT_SET_UP, 7: IDENTITY_REFUSED, 5: UNREACHABLE, 8: UNEXPLAINED}
BLOCKING = {4: NOT_SET_UP, 7: IDENTITY_REFUSED}  # restarting can't help until an admin fixes agent/.env

FIX = {
    4: "agent/.env has no COGNITO_AGENT_CLIENT_SECRET (or COGNITO_DOMAIN / COGNITO_AGENT_CLIENT_ID): "
       "fill it in, then press Try again in the chat",
    7: "Cognito refused COGNITO_AGENT_CLIENT_ID / COGNITO_AGENT_CLIENT_SECRET in agent/.env: copy the "
       "agent client's current secret, then press Try again in the chat",
    5: "the agent could not reach Cognito or the broker: check the network and that the gateway is running",
    8: "the agent failed before it could report why: its own output above names the error",
}


def _detail_key(sid: str, message_id: str) -> dict[str, str]:
    return {"pk": f"CHATDETAIL#{sid}", "sk": message_id}


def save_detail(sid: str, message_id: str, detail: str) -> None:
    get_gateway().store.table.put_item(Item={**_detail_key(sid, message_id), "detail": detail})


def details_for(sid: str) -> dict[str, str]:
    """message id -> technical detail, for an admin reading their own chat."""
    from boto3.dynamodb.conditions import Key

    items = get_gateway().store.table.query(KeyConditionExpression=Key("pk").eq(f"CHATDETAIL#{sid}"),
                                            ConsistentRead=True).get("Items", [])
    return {item["sk"]: item["detail"] for item in items}


def post(sid: str, text: str, detail: str | None = None) -> None:
    """An assistant message written by the server on the agent's behalf."""
    message = ChatMessageItem(id=str(uuid.uuid4()), ts=datetime.now(timezone.utc).isoformat(), role="assistant", text=text)
    get_gateway().store.put_chat_message(sid, message)
    if detail:
        save_detail(sid, message.id, detail)


def report_exit(sid: str, code: int | None) -> None:
    """The supervisor saw the agent exit. Explain it in the chat unless the agent already did."""
    if code in SAID_ALREADY:
        return
    text = STAFF_WORDS.get(code, CRASHED)
    log.warning("agent exited sid=%s code=%s: %s", sid, code, FIX.get(code, "see the agent's output above"))
    post(sid, text, f"agent exit code {code}")
