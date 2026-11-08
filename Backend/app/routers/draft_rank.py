"""Which held draft to review first: a small, deterministic urgency score the staffer can read.

Inputs are trusted data only: the draft's category (set by the gateway from the inbox, never by the agent),
the original inbox email it replies to (only when the reply goes to that email's sender, the same check the
gateway uses for the category), and how long the draft has waited. The agent's own draft text is never read,
so the agent cannot raise its own rank. The rank only orders the review list; it changes no permission.
"""

import re
from datetime import datetime

from mandate_contracts.ddb import DraftItem, EmailItem

from identity_gateway.autonomy import LABELS

# How much each kind of message matters on its own (personal or benefits data first, newsletters last).
KIND_WEIGHT = {
    "sensitive": 40,
    "case_status": 30,
    "scheduling": 20,
    "info_request": 15,
    "new_conversation": 10,
    "social_post": 10,
    "staff_memo": 5,
    "suspicious": 5,
    "newsletter": 0,
}
# English plus the other languages in the office's inbox (Vietnamese, Spanish), so those writers aren't ranked lower.
URGENT_WORDS = ("urgent", "emergency", "eviction", "evicted", "medical", "hospital", "deadline", "today", "asap",
                "immediately", "shut off", "homeless", "gấp", "khẩn cấp", "urgente", "emergencia", "desalojo")
REPEAT_PHRASES = ("third time", "second time", "again", "follow up", "follow-up", "no reply", "nobody", "haven't heard",
                  "still waiting", "called back")
URGENT_BONUS = 30
REPEAT_BONUS = 15
AGE_POINTS_PER_HOUR = 2
AGE_CAP = 20
HIGH_AT = 60
MEDIUM_AT = 35


def _mentions(text: str, words: tuple[str, ...]) -> list[str]:
    return [w for w in words if re.search(rf"\b{re.escape(w)}\b", text)]


def _age(minutes: int) -> str:
    if minutes < 60:
        return "waiting under an hour"
    if minutes < 48 * 60:
        return f"waiting {minutes // 60} h"
    return f"waiting {minutes // (24 * 60)} days"


def rank_draft(draft: DraftItem, email: EmailItem | None, now: datetime) -> dict:
    """{urgency: high|medium|low, score: int, reason: one line} for one held draft."""
    replies_to_sender = (
        draft.tool == "email_send"
        and email is not None
        and email.from_.strip().lower() == draft.args.get("to", "").strip().lower()
    )
    text = f"{email.subject}\n{email.body}".lower() if replies_to_sender else ""
    urgent = _mentions(text, URGENT_WORDS)
    repeat = bool(_mentions(text, REPEAT_PHRASES))
    minutes = max(0, int((now - datetime.fromisoformat(draft.created_at)).total_seconds() // 60))

    score = KIND_WEIGHT.get(draft.category, 10)
    score += URGENT_BONUS if urgent else 0
    score += REPEAT_BONUS if repeat else 0
    score += min(AGE_CAP, minutes * AGE_POINTS_PER_HOUR // 60)

    reason = [LABELS.get(draft.category, draft.category)]
    if urgent:
        named = ", ".join(f"'{w}'" for w in urgent[:2])
        reason.append(f"mentions {named}" + (f" and {len(urgent) - 2} more" if len(urgent) > 2 else ""))
    if repeat:
        reason.append("wrote before")
    reason.append(_age(minutes))
    urgency = "high" if score >= HIGH_AT else "medium" if score >= MEDIUM_AT else "low"
    return {"urgency": urgency, "score": score, "reason": " · ".join(reason)}
