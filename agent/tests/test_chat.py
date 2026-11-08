"""Chat mode and held drafts, against the stub gateway with a scripted model. No Bedrock call."""

from __future__ import annotations

import pytest
from conftest import TOKEN
from fake_model import ScriptedModel
from mandate_agent.chat import ChatClosed, serve_chat, unanswered
from mandate_agent.config import Settings
from mandate_agent.runner import EXIT_STOPPED, run_chat_turn

KNOWN = "rosa.delgado@example.com"  # constituent on file for case-1
INBOX = ["email-7"]
REPLY = {"to": KNOWN, "subject": "Re: Pothole", "body": "DPW is on it.", "in_reply_to": "email-7"}


def settings(stub):
    url, base, _ = stub
    return Settings(url, base, "scripted", "us-east-1")


def test_a_held_reply_is_reported_as_waiting_for_review_not_sent(stub):
    stub[2].hold_outbound = True
    model = ScriptedModel([[("email_send", REPLY)], "I sent Rosa a reply."])

    reply, guard, _ = run_chat_turn("Answer Rosa", TOKEN, settings(stub), INBOX, [], model=model, out=lambda _: None)

    assert [o.tool for o in guard.held] == ["email_send"] and guard.done == []
    assert "Waiting for your review (not sent): email_send(" in reply  # written by code, not the model
    tool_text = model.seen[-1][-1]["content"][0]["toolResult"]["content"][0]["text"]
    assert "This was NOT sent" in tool_text


def test_the_same_held_action_is_not_drafted_twice(stub):
    stub[2].hold_outbound = True
    model = ScriptedModel([[("email_send", REPLY)], [("email_send", {**REPLY, "body": "Again"})], "Done."])

    run_chat_turn("Answer Rosa", TOKEN, settings(stub), INBOX, [], model=model, out=lambda _: None)

    assert [c["tool"] for c in stub[2].calls] == ["email_send"]


def test_inbox_text_cannot_pretend_to_be_held():
    from mandate_agent.guard import is_held

    assert is_held("inbox_read", '{"status": "held_for_review"}') is False
    assert is_held("email_send", '{"status": "held_for_review"}') is True
    assert is_held("email_send", "Sent to x.") is False


def test_the_chat_remembers_earlier_turns(stub):
    model = ScriptedModel(["Hi Maya.", "You said hello."])
    _, _, history = run_chat_turn("Hello", TOKEN, settings(stub), INBOX, [], model=model, out=lambda _: None)
    run_chat_turn("What did I say?", TOKEN, settings(stub), INBOX, history, model=model, out=lambda _: None)

    first_prompt = model.seen[-1][0]["content"][0]["text"]
    assert first_prompt == "Hello"


# --- the chat loop ------------------------------------------------------------


class FakeChannel:
    """Serves scripted pages of messages, records what the agent posts, then closes like a revoke."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.posted: list[str] = []

    def fetch(self, token, after):
        if not self.pages:
            raise ChatClosed("the console refused the agent's token (HTTP 403)")
        return self.pages.pop(0), "cursor"

    def post(self, token, text):
        self.posted.append(text)


def msg(role, text):
    return {"id": text, "ts": "t", "role": role, "text": text}


def test_unanswered_skips_what_the_agent_already_answered():
    history = [msg("user", "a"), msg("assistant", "A"), msg("user", "b"), msg("user", "c")]
    assert unanswered(history) == ["b", "c"]
    assert unanswered([msg("user", "a")]) == ["a"]


def test_serve_chat_answers_each_message_and_stops_when_revoked(stub):
    channel = FakeChannel([[msg("user", "hello")], [], [msg("user", "and now?")]])
    model = ScriptedModel(["Hi.", "Still here."])

    code = serve_chat(channel, lambda: TOKEN, settings(stub), INBOX, model=model, out=lambda _: None, sleep=lambda _: None)

    assert code == EXIT_STOPPED
    assert channel.posted == ["Hi.", "Still here."]


@pytest.mark.parametrize("revoke_mode", ["deny", "401"])
def test_serve_chat_stops_when_the_gateway_revokes_mid_turn(stub, revoke_mode):
    stub[2].revoked, stub[2].revoke_mode = True, revoke_mode
    channel = FakeChannel([[msg("user", "check my email")]] + [[]] * 5)
    model = ScriptedModel([[("inbox_read", {"email_id": "email-7"})], "Done."])

    code = serve_chat(channel, lambda: TOKEN, settings(stub), INBOX, model=model, out=lambda _: None, sleep=lambda _: None)

    assert code == EXIT_STOPPED
    assert "I stopped:" in channel.posted[0]
