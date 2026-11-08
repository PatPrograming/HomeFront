"""Ranking held drafts by urgency and importance (pure scorer + the /drafts order). No AWS."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mandate_contracts.ddb import DraftItem, EmailItem

from app.routers.draft_rank import rank_draft

from .test_assistant_api import CASE_EMAIL, SID, as_staff, hold_reply, session  # noqa: F401 (fixtures)
from .test_gateway_api import _env, client, gateway, store, table  # noqa: F401 (fixtures)

NOW = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)


def draft(category="case_status", to="a@example.com", body="Thanks", minutes_old=0, in_reply_to="email-1", tool="email_send"):
    args = {"to": to, "subject": "Re: x", "body": body, "in_reply_to": in_reply_to} if tool == "email_send" else {"account": "official", "text": body}
    return DraftItem(
        status="pending", sid="s", agent="agent-1", acting_for="maya", office="rep-x", tool=tool, category=category,
        args=args, created_at=(NOW - timedelta(minutes=minutes_old)).isoformat(),
    )


def email(body="Any news?", sender="a@example.com", subject="Question", kind="case_status"):
    return EmailItem(from_=sender, subject=subject, body=body, kind=kind)


def test_personal_data_outranks_case_updates_which_outrank_newsletters():
    sensitive = rank_draft(draft("sensitive"), email(kind="sensitive"), NOW)
    case = rank_draft(draft("case_status"), email(), NOW)
    news = rank_draft(draft("newsletter"), email(kind="newsletter"), NOW)
    assert sensitive["score"] > case["score"] > news["score"]


def test_urgent_words_in_the_original_email_raise_it_and_are_named_in_the_reason():
    calm = rank_draft(draft(), email("Any news on my case?"), NOW)
    urgent = rank_draft(draft(), email("I face eviction on Friday, please help"), NOW)
    assert urgent["score"] > calm["score"]
    assert urgent["urgency"] == "high" and calm["urgency"] != "high"
    assert "mentions 'eviction'" in urgent["reason"]


def test_a_repeat_contact_and_age_raise_it():
    first = rank_draft(draft(), email("Any news?"), NOW)
    again = rank_draft(draft(), email("This is the third time I am writing."), NOW)
    old = rank_draft(draft(minutes_old=600), email("Any news?"), NOW)
    assert again["score"] > first["score"] and "wrote before" in again["reason"]
    assert old["score"] > first["score"] and "waiting 10 h" in old["reason"]


def test_the_agents_own_draft_text_never_raises_its_rank():
    plain = rank_draft(draft(body="Thanks for writing."), email(), NOW)
    pushy = rank_draft(draft(body="URGENT eviction emergency medical deadline today!!"), email(), NOW)
    assert pushy == plain


def test_an_urgent_email_from_someone_else_cannot_be_borrowed():
    # The agent sets in_reply_to; it only counts when the reply goes to that email's sender (as the gateway checks).
    borrowed = rank_draft(draft(to="other@example.com"), email("eviction today, urgent"), NOW)
    assert "eviction" not in borrowed["reason"] and borrowed["urgency"] != "high"


def test_a_post_ranks_without_an_email():
    post = rank_draft(draft("social_post", tool="social_post"), None, NOW)
    assert post["urgency"] in {"low", "medium"} and post["reason"].startswith("Posts on the official account")


def test_drafts_come_back_ranked_with_urgency_score_and_reason(client, session, gateway, store):
    as_staff()
    calm = hold_reply(gateway)
    store.put_email(
        "email-urgent",
        EmailItem(from_="jane.doe@example.com", subject="Eviction notice", body="Urgent: eviction hearing today", kind="case_status"),
    )
    args = {"to": "jane.doe@example.com", "subject": "Re: Eviction", "body": "We are on it.", "in_reply_to": "email-urgent"}
    from .test_assistant_api import agent_headers

    token = agent_headers()["Authorization"].removeprefix("Bearer ")
    urgent = gateway.guarded("email_send", args, token).result["draftId"]

    drafts = client.get("/api/v1/drafts").json()["drafts"]
    assert [d["id"] for d in drafts] == [urgent, calm]  # most urgent first, though it was held second
    assert drafts[0]["urgency"] == "high" and "eviction" in drafts[0]["reason"]
    assert isinstance(drafts[0]["score"], int) and drafts[0]["score"] > drafts[1]["score"]


def test_urgent_words_count_in_vietnamese_and_spanish_too():
    vi = rank_draft(draft(), email("Tôi cần gia hạn hộ chiếu gấp"), NOW)
    es = rank_draft(draft(), email("Es urgente, tengo una orden de desalojo"), NOW)
    assert "mentions 'gấp'" in vi["reason"] and vi["urgency"] == "high"
    assert "'urgente'" in es["reason"] and es["urgency"] == "high"
