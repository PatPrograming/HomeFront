"""Research -> one draft -> revise until satisfied (check C14).

Offline: a scripted model talks to the stub gateway. No Bedrock call.

Assumed stub shape (the spec does not fix it, so the implementer should match or tell the test author):
`StubState.drafts` is a dict of draftId -> draft. A draft is a dict (or object) with `tool`, `args`
(the editable fields live here, for example args["text"] or args["subject"]), `version` (starts at 1)
and `sid` (the session that made it). `draft_revise` takes `draft_id` plus the fields to change.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from conftest import TOKEN
from fake_model import ScriptedModel
from mandate_agent.config import Settings
from mandate_agent.runner import CHAT_SYSTEM_PROMPT, run_chat_turn
from mandate_contracts import TOOLS
from test_stub_server import _session_call

INBOX = ["email-7"]
PROMPT = " ".join(CHAT_SYSTEM_PROMPT.lower().split())


def has(pattern: str) -> bool:
    return re.search(pattern, PROMPT) is not None


# --- the prompt ---------------------------------------------------------------


def test_the_prompt_says_to_ask_for_missing_details_before_drafting():
    assert has(r"ask[^.]{0,80}(missing|further|more)[^.]{0,20}(detail|information)") or has(
        r"(missing|further)[^.]{0,20}(detail|information)[^.]{0,80}\bask"
    )


def test_the_prompt_says_to_research_with_the_research_tools_before_drafting():
    assert has(r"research[^.]{0,200}before[^.]{0,40}draft") or has(r"before[^.]{0,40}draft[^.]{0,200}research")


def test_the_prompt_says_to_produce_one_draft_not_several():
    assert has(r"\b(one|single|exactly one) draft\b")


def test_the_prompt_says_a_social_draft_names_the_platform():
    assert has(r"platform[^.]{0,200}(social|post)|(social|post)[^.]{0,200}platform")


def test_the_prompt_says_an_email_draft_names_the_recipient_and_subject():
    assert has(r"recipient[^.]{0,200}subject|subject[^.]{0,200}recipient")


@pytest.mark.parametrize("platform", ["bluesky", "linkedin", "facebook"])
def test_the_prompt_lists_the_supported_platforms(platform):
    assert platform in PROMPT


def test_the_prompt_lists_x_as_a_supported_platform():
    assert has(r"\bx\b[^.]{0,60}bluesky|bluesky[^.]{0,60}\bx\b")


def test_the_prompt_names_draft_revise_for_follow_up_edits():
    assert "draft_revise" in CHAT_SYSTEM_PROMPT


def test_the_prompt_gives_make_it_shorter_as_a_follow_up_to_revise_not_redraft():
    assert "make it shorter" in PROMPT
    revise = PROMPT.index("draft_revise")
    window = PROMPT[max(0, revise - 400) : revise + 400]
    assert re.search(r"instead of (drafting|writing|creating|making)[^.]{0,20}(again|a new|another)|not (a )?(new|another) draft", window)


def test_the_prompt_says_a_revise_draft_message_means_calling_draft_revise_on_that_id():
    assert "revise draft" in PROMPT
    at = PROMPT.index("revise draft")
    window = PROMPT[max(0, at - 300) : at + 400]
    assert "draft_revise" in window
    assert re.search(r"\bid\b|draftid|draft_id|<", window)


def test_the_prompt_tells_maya_she_can_edit_the_draft_card_directly():
    assert has(r"edit[^.]{0,80}\bdirectly|directly[^.]{0,80}\bedit") and "card" in PROMPT


# --- the stub gateway ----------------------------------------------------------


def settings(stub):
    url, base, _ = stub
    return Settings(url, base, "scripted", "us-east-1")


def drafts(state) -> dict:
    assert hasattr(state, "drafts"), "StubState needs a `drafts` dict of draftId -> draft"
    return state.drafts


def field(draft, name):
    return draft[name] if isinstance(draft, dict) else getattr(draft, name)


def tool_texts(model):
    texts = []
    for m in model.seen[-1]:
        for block in m["content"]:
            if "toolResult" in block:
                texts += [c["text"] for c in block["toolResult"]["content"]]
    return texts


def test_draft_revise_is_a_contract_tool():
    assert "draft_revise" in TOOLS


def test_the_stub_serves_every_contract_tool_including_draft_revise(stub):
    listed = asyncio.run(_session_call(stub[0], lambda s: s.list_tools())).tools
    names = sorted(t.name for t in listed)
    assert names == sorted(TOOLS)
    assert "draft_revise" in names


def test_the_stub_denies_revising_a_draft_from_another_session(stub):
    url, _, state = stub
    state.drafts = {
        "draft-other": {
            "tool": "social_post",
            "args": {"account": "official", "text": "Original text"},
            "version": 1,
            "status": "pending",
            "sid": "another-session-sid",
        }
    }
    result = asyncio.run(
        _session_call(url, lambda s: s.call_tool("draft_revise", {"draft_id": "draft-other", "text": "Hijacked"}))
    )
    text = result.content[0].text
    assert text.startswith("DENIED"), text
    assert "denied by policy" in text
    assert field(state.drafts["draft-other"], "version") == 1
    assert field(state.drafts["draft-other"], "args")["text"] == "Original text"
    assert state.calls[-1]["tool"] == "draft_revise"
    assert state.calls[-1]["decision"] == "DENY"


# --- two scripted turns --------------------------------------------------------


def chat(stub, script, message, history):
    model = ScriptedModel(script)
    reply, guard, history = run_chat_turn(message, TOKEN, settings(stub), INBOX, history, model=model, out=lambda _: None)
    return reply, guard, history, model


def only_draft_id(state):
    assert len(drafts(state)) == 1
    return next(iter(state.drafts))


def test_research_then_one_held_social_post_then_make_it_shorter_revises_that_draft(stub):
    state = stub[2]
    state.hold_outbound = True
    long_text = "Join us Tuesday at 6pm at the community center for our annual town hall on road repair funding."
    short_text = "Town hall Tuesday 6pm, community center."

    _, guard1, history, _ = chat(
        stub,
        [
            [("census_query", {"year": "2022", "dataset": "acs/acs5", "get": "NAME,B01001_001E", "for": "state:06"})],
            [("social_post", {"account": "official", "text": long_text, "platform": "bluesky"})],
            "Here is a draft for Bluesky. You can edit it in the card.",
        ],
        "Draft a post about the town hall",
        [],
    )
    assert [c["tool"] for c in state.calls] == ["census_query", "social_post"]
    draft_id = only_draft_id(state)
    assert field(state.drafts[draft_id], "version") == 1
    assert [o.tool for o in guard1.held] == ["social_post"]

    _, guard2, _, model = chat(
        stub,
        [
            lambda: [("draft_revise", {"draft_id": draft_id, "text": short_text})],
            "Shortened.",
        ],
        "make it shorter",
        history,
    )
    assert [c["tool"] for c in state.calls] == ["census_query", "social_post", "draft_revise"]
    assert state.calls[-1]["decision"] == "ALLOW"
    assert guard2.denied == []
    assert len(state.drafts) == 1, "revising must not create a second draft"
    draft = state.drafts[draft_id]
    assert field(draft, "version") == 2
    assert field(draft, "args")["text"] == short_text
    assert not tool_texts(model)[-1].startswith("DENIED")


def test_a_held_email_then_a_revision_changes_the_subject_of_the_same_draft(stub):
    state = stub[2]
    state.hold_outbound = True

    _, _, history, _ = chat(
        stub,
        [
            [("email_send", {"to": "rosa.delgado@example.com", "subject": "Re: Pothole update", "body": "DPW is on it."})],
            "I drafted the reply. You can edit the card.",
        ],
        "Draft a reply to Rosa about the pothole",
        [],
    )
    draft_id = only_draft_id(state)
    assert field(state.drafts[draft_id], "version") == 1

    _, guard2, _, _ = chat(
        stub,
        [
            lambda: [("draft_revise", {"draft_id": draft_id, "subject": "Pothole fixed on Elm Street"})],
            "Subject updated.",
        ],
        "change the subject to say it is fixed",
        history,
    )
    assert [c["tool"] for c in state.calls] == ["email_send", "draft_revise"]
    assert guard2.denied == []
    assert len(state.drafts) == 1
    draft = state.drafts[draft_id]
    assert field(draft, "version") == 2
    assert field(draft, "args")["subject"] == "Pothole fixed on Elm Street"
    assert field(draft, "args")["to"] == "rosa.delgado@example.com", "recipient never changes by a subject edit"
    assert json.dumps(field(draft, "args")).count("DPW is on it.") == 1, "body kept"


def test_a_revise_draft_chat_message_from_the_card_calls_draft_revise_on_that_draft(stub):
    state = stub[2]
    state.hold_outbound = True
    long_text = "Join us Tuesday at 6pm at the community center for our annual town hall on road repair funding."
    short_text = "Town hall Tuesday 6pm."

    _, _, history, _ = chat(
        stub,
        [[("social_post", {"account": "official", "text": long_text, "platform": "x"})], "Draft ready."],
        "Draft a post about the town hall",
        [],
    )
    draft_id = only_draft_id(state)

    _, guard2, _, _ = chat(
        stub,
        [lambda: [("draft_revise", {"draft_id": draft_id, "text": short_text})], "Shortened."],
        f"Revise draft {draft_id}: make it shorter",
        history,
    )
    assert [c["tool"] for c in state.calls] == ["social_post", "draft_revise"]
    assert state.calls[-1]["args"]["draft_id"] == draft_id
    assert guard2.denied == []
    assert len(state.drafts) == 1
    assert field(state.drafts[draft_id], "version") == 2
    assert field(state.drafts[draft_id], "args")["text"] == short_text
