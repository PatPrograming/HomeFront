"""Share links: approved social drafts become prefilled compose links.

HomeFront holds no social login, so nothing is published. The result says
"handed_off" and the person presses Post on the platform.
"""
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path
from tests.helpers import ADMIN

from mcp_tools import share_links  # noqa: E402
from mcp_tools.share_links import ShareLinkError, build_post_link, build_reply_link  # noqa: E402

# Every call signs in as an admin with our Cognito login (tests/helpers.py).
client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login", "old_tables")
LINK = "https://www.agency.gov/flood-update"


def _query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# --- the link builder -------------------------------------------------------------------


def test_x_post_is_prefilled_and_encoded():
    out = build_post_link("x", "Roads closed & flooded: https://www.agency.gov/a?b=1")
    parts = urlsplit(out["share_url"])
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "x.com", "/intent/post")
    assert _query(out["share_url"])["text"] == "Roads closed & flooded: https://www.agency.gov/a?b=1"
    assert "+" not in parts.query  # spaces are %20
    assert out["status"] == "handed_off" and out["published"] is False
    assert out["mode"] == "prefilled"


def test_x_counts_links_as_23_characters():
    text = "a" * 250 + " " + LINK  # 250 + space + 23 = 274, though the raw text is longer
    out = build_post_link("x", text)
    assert out["char_count"] == 274 and out["over_limit"] is False
    assert build_post_link("x", "a" * 281)["over_limit"] is True
    assert build_post_link("x", "a" * 281)["warnings"]


def test_bluesky_post():
    out = build_post_link("bluesky", "Town hall Tuesday")
    assert urlsplit(out["share_url"]).netloc == "bsky.app"
    assert _query(out["share_url"]) == {"text": "Town hall Tuesday"}


def test_reddit_title_and_body():
    out = build_post_link("reddit", "Flood update\nRoads 4 and 9 are closed.")
    assert _query(out["share_url"]) == {"type": "TEXT", "title": "Flood update", "text": "Roads 4 and 9 are closed."}


def test_reddit_bare_link_is_a_link_post():
    out = build_post_link("reddit", LINK)
    assert _query(out["share_url"]) == {"type": "LINK", "url": LINK, "title": LINK}


@pytest.mark.parametrize(
    "platform,host,param",
    [("facebook", "www.facebook.com", "u"), ("linkedin", "www.linkedin.com", "url")],
)
def test_link_only_platforms_carry_just_the_link(platform, host, param):
    out = build_post_link(platform, f"Flood update, details here: {LINK}.")
    assert urlsplit(out["share_url"]).netloc == host
    assert _query(out["share_url"]) == {param: LINK}  # trailing "." is not part of the link
    assert out["mode"] == "link_only"
    assert out["copy_text"].startswith("Flood update")


def test_link_only_platform_without_a_link_falls_back_to_copy_and_open():
    out = build_post_link("facebook", "No link in this one")
    assert out["mode"] == "copy_and_open"
    assert urlsplit(out["share_url"]).netloc == "www.facebook.com"
    assert out["copy_text"] == "No link in this one"


def test_aliases_and_case():
    assert build_post_link(" Twitter ", "hi")["platform"] == "x"
    assert build_post_link("BSKY", "hi")["platform"] == "bluesky"


@pytest.mark.parametrize("platform", ["instagram", "tiktok", "", None])
def test_unsupported_platforms_raise(platform):
    with pytest.raises(ShareLinkError):
        build_post_link(platform, "hi")


def test_instagram_message_says_so():
    with pytest.raises(ShareLinkError, match="Instagram"):
        build_post_link("instagram", "hi")


@pytest.mark.parametrize("text", ["", "   ", None, "a" * (share_links.MAX_TEXT_CHARS + 1)])
def test_empty_or_oversized_text_raises(text):
    with pytest.raises(ShareLinkError):
        build_post_link("x", text)


def test_x_reply_with_id_or_status_url():
    for target in ("1234567890", "https://x.com/cityhall/status/1234567890", "https://twitter.com/a/status/1234567890?s=20"):
        out = build_reply_link("x", target, "Thanks, we'll look into it.")
        assert _query(out["share_url"]) == {"text": "Thanks, we'll look into it.", "in_reply_to": "1234567890"}
        assert out["mode"] == "prefilled"


def test_reply_on_other_platforms_opens_the_post_for_pasting():
    out = build_reply_link("reddit", "https://www.reddit.com/r/city/comments/abc/post/", "Thanks")
    assert out["mode"] == "copy_and_open"
    assert out["share_url"] == "https://www.reddit.com/r/city/comments/abc/post/"


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/phish",  # off-platform host
        "https://x.com.evil.example/status/1",  # look-alike host
        "javascript:alert(1)",
        "not a url",
        "",
    ],
)
def test_reply_target_off_platform_never_becomes_the_link(target):
    out = build_reply_link("bluesky", target, "Thanks")
    assert out["share_url"] == "https://bsky.app/"
    out = build_reply_link("x", target, "Thanks")
    assert urlsplit(out["share_url"]).netloc == "x.com"
    assert "evil" not in out["share_url"] and "javascript" not in out["share_url"]


def test_every_share_url_stays_on_its_platform():
    allowed = share_links.allowed_share_hosts()
    for platform in share_links.SUPPORTED:
        for out in (
            build_post_link(platform, f"hello {LINK}"),
            build_post_link(platform, "hello"),
            build_reply_link(platform, "https://evil.example/x", "hi"),
        ):
            host = urlsplit(out["share_url"]).hostname
            assert any(host == d or host.endswith("." + d) for d in allowed[platform]), (platform, host)


# --- through the gateway ----------------------------------------------------------------


def _social_agent():
    r = client.post(
        "/api/v1/agents",
        json={"agent_id": f"agent-share-{uuid.uuid4().hex[:8]}", "name": "Social", "agency": "City", "kind": "social"},
    )
    assert r.status_code == 201, r.text
    agent = r.json()
    token = client.post("/api/v1/auth/token", json={"agent_id": agent["agent_id"]}).json()
    return agent, {"X-Agent-Token": token["access_token"]}, token["session_id"]


def _call(headers, tool, args):
    return client.post("/api/v1/agents/tool-call", json={"tool": tool, "arguments": args}, headers=headers)


def test_tool_schema_lists_only_supported_platforms():
    from mcp_tools.registry import get_tool

    for name in ("social.post", "social.reply"):
        enum = get_tool(name).input_schema["properties"]["platform"]["enum"]
        assert enum == ["x", "bluesky", "reddit", "facebook", "linkedin"]


def test_approved_post_hands_off_a_share_link_and_is_audited():
    agent, headers, session = _social_agent()
    queued = _call(headers, "social.post", {"platform": "x", "text": f"Flood update {LINK}"}).json()
    assert queued["decision"] == "pending_approval"

    done = client.post(f"/api/v1/approvals/{queued['approval_id']}/approve", json={}).json()
    assert done["status"] == "executed"
    result = done["result"]
    assert result["status"] == "handed_off" and result["published"] is False
    assert urlsplit(result["share_url"]).netloc == "x.com"
    assert _query(result["share_url"])["text"] == f"Flood update {LINK}"

    events = client.get("/api/v1/audit", params={"agent_id": agent["agent_id"], "trace_id": session}).json()
    [call] = [e for e in events if e["type"] == "tool_call"]
    assert "not published by HomeFront" in call["detail"]


def test_reviewer_edits_change_the_link():
    agent, headers, _ = _social_agent()
    queued = _call(headers, "social.post", {"platform": "x", "text": "Draft"}).json()
    done = client.post(
        f"/api/v1/approvals/{queued['approval_id']}/approve",
        json={"arguments": {"platform": "bluesky", "text": "Edited by a person"}},
    ).json()
    assert done["edited"] is True
    assert urlsplit(done["result"]["share_url"]).netloc == "bsky.app"
    assert _query(done["result"]["share_url"])["text"] == "Edited by a person"


@pytest.mark.parametrize("platform", ["instagram", "tiktok"])
def test_unsupported_platform_is_denied_before_it_reaches_a_reviewer(platform):
    agent, headers, _ = _social_agent()
    r = _call(headers, "social.post", {"platform": platform, "text": "hi"})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "platform_not_supported"
    pending = client.get("/api/v1/approvals", params={"status": "pending"}).json()
    assert not [a for a in pending if a["agent_id"] == agent["agent_id"]]


def test_reviewer_cannot_approve_an_unsupported_platform():
    agent, headers, _ = _social_agent()
    queued = _call(headers, "social.post", {"platform": "x", "text": "hi"}).json()
    r = client.post(
        f"/api/v1/approvals/{queued['approval_id']}/approve",
        json={"arguments": {"platform": "instagram", "text": "hi"}},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "no_longer_authorized"
    # still pending, so the reviewer can fix it
    assert client.get(f"/api/v1/approvals/{queued['approval_id']}").json()["status"] == "pending"


def test_reply_queues_and_hands_off():
    agent, headers, _ = _social_agent()
    queued = _call(
        headers,
        "social.reply",
        {"platform": "x", "reply_to": "https://x.com/cityhall/status/42", "text": "Thanks for asking."},
    ).json()
    done = client.post(f"/api/v1/approvals/{queued['approval_id']}/approve", json={}).json()
    assert _query(done["result"]["share_url"])["in_reply_to"] == "42"


# --- agent platforms (icons on the agent card) -------------------------------------------


def _create(**body):
    return client.post(
        "/api/v1/agents",
        json={"agent_id": f"agent-plat-{uuid.uuid4().hex[:8]}", "name": "S", "agency": "City", **body},
    )


def test_social_agent_keeps_its_platforms_normalized_and_deduped():
    r = _create(kind="social", platforms=["Twitter", "x", "bluesky"])
    assert r.status_code == 201, r.text
    assert r.json()["platforms"] == ["x", "bluesky"]
    assert client.get(f"/api/v1/agents/{r.json()['agent_id']}").json()["platforms"] == ["x", "bluesky"]


def test_agents_default_to_no_platforms():
    assert _create(kind="email").json()["platforms"] == []


@pytest.mark.parametrize("platforms", [["instagram"], ["tiktok"]])
def test_unsupported_agent_platform_is_rejected(platforms):
    assert _create(kind="social", platforms=platforms).status_code == 422


def test_only_social_agents_have_platforms():
    assert _create(kind="email", platforms=["x"]).status_code == 422
