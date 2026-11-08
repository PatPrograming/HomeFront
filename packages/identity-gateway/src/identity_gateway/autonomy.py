"""Earned autonomy: which outbound calls the agent may make alone, and when to offer more.

Every `email_send` and `social_post` that Cedar allows is sorted into a category. Unless the
staffer has set that category to "auto", the gateway holds the call as a draft for review.
The staffer's approvals are the evidence: after enough unchanged approvals the console offers
to let the agent handle the category alone. The staffer decides; nothing is promoted silently.

The category never comes from the agent. A reply's category is the kind of the inbox email it
answers, read from DynamoDB, and only when the recipient is that email's sender.
"""

from __future__ import annotations

from dataclasses import dataclass

from mandate_contracts.ddb import AutonomyItem

# Reply categories, from the `kind` set when an email is triaged into the inbox.
EMAIL_CATEGORIES = ("case_status", "info_request", "scheduling", "newsletter", "sensitive", "staff_memo", "suspicious")
NEW_CONVERSATION = "new_conversation"  # an email that answers nothing in the inbox
SOCIAL_CATEGORY = "social_post"

# Never sent without review, however many approvals: personal data, instructions that claim to
# come from staff, mail that looks like an attack, and writing to someone out of the blue.
LOCKED = frozenset({"sensitive", "staff_memo", "suspicious", "other", NEW_CONVERSATION})

# Unchanged approvals needed before the console offers "handle these yourself". Public posts are
# harder to take back than an email, so they need more.
PROMOTE_AFTER = {SOCIAL_CATEGORY: 5}
DEFAULT_PROMOTE_AFTER = 3
MIN_UNCHANGED_SHARE = 0.9

LABELS = {
    "case_status": "Case status updates",
    "info_request": "Information requests",
    "scheduling": "Scheduling and tours",
    "newsletter": "Newsletters",
    "sensitive": "Personal or benefits data",
    "staff_memo": "Instructions from 'staff'",
    "suspicious": "Suspicious mail",
    "other": "Uncategorized mail",
    NEW_CONVERSATION: "New conversations",
    SOCIAL_CATEGORY: "Posts on the official account",
}

CATEGORIES = (*EMAIL_CATEGORIES, NEW_CONVERSATION, SOCIAL_CATEGORY)


@dataclass(frozen=True)
class CategoryView:
    """One row of the console's autonomy panel."""

    category: str
    label: str
    level: str
    locked: bool
    approved_unchanged: int
    edited: int
    rejected: int
    suggest_auto: bool

    def as_json(self) -> dict:
        return {
            "category": self.category,
            "label": self.label,
            "level": self.level,
            "locked": self.locked,
            "approvedUnchanged": self.approved_unchanged,
            "edited": self.edited,
            "rejected": self.rejected,
            "suggestAuto": self.suggest_auto,
        }


def reviews(item: AutonomyItem) -> int:
    return item.approved_unchanged + item.edited + item.rejected


def should_suggest_auto(category: str, item: AutonomyItem) -> bool:
    """Enough recent evidence that the staffer sends this category as drafted."""
    if category in LOCKED or item.level == "auto":
        return False
    needed = PROMOTE_AFTER.get(category, DEFAULT_PROMOTE_AFTER)
    since_snooze = reviews(item) - item.snoozed_at
    return (
        item.approved_unchanged >= needed
        and since_snooze >= needed
        and item.approved_unchanged / reviews(item) >= MIN_UNCHANGED_SHARE
    )


def view(category: str, item: AutonomyItem) -> CategoryView:
    return CategoryView(
        category=category,
        label=LABELS.get(category, category),
        level=item.level,
        locked=category in LOCKED,
        approved_unchanged=item.approved_unchanged,
        edited=item.edited,
        rejected=item.rejected,
        suggest_auto=should_suggest_auto(category, item),
    )
