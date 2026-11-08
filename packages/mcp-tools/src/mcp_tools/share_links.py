"""Share links: prefilled compose URLs for platforms where the person publishes.

HomeFront never holds a social login. After a human approves a social.post or
social.reply, the gateway builds a link that opens the platform's own compose
screen with the draft filled in. The person is already signed in there and
presses Post themselves, so the platform sees an ordinary user action and
HomeFront can only claim "handed off", never "published".

What each platform carries in the link (check these before relying on them;
platforms change their compose parameters):
- x         text (and a reply target)           prefilled
- bluesky   text                                prefilled
- reddit    title, plus body text or a link     prefilled
- facebook  a link only (the text is ignored)   link_only
- linkedin  a link only                         link_only
Images can't ride along in any of them.

Every URL is built here from a fixed host and URL-encoded values. Nothing the
agent supplies can pick the host, except a reply target, which is accepted
only if it is already on that platform's own domain.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

# A compose URL carries the whole draft, so cap the draft well under common
# URL-length limits.
MAX_TEXT_CHARS = 3000
REDDIT_TITLE_MAX = 300
X_LINK_COST = 23  # X counts every link as 23 characters

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_X_STATUS_RE = re.compile(r"/status(?:es)?/(\d+)")


@dataclass(frozen=True)
class SharePlatform:
    id: str
    name: str
    home: str  # where to send the person when nothing can be prefilled
    domains: tuple[str, ...]  # hosts a reply target may live on
    char_limit: int | None


SHARE_PLATFORMS: dict[str, SharePlatform] = {
    "x": SharePlatform("x", "X", "https://x.com/compose/post", ("x.com", "twitter.com"), 280),
    "bluesky": SharePlatform("bluesky", "Bluesky", "https://bsky.app/", ("bsky.app",), 300),
    "reddit": SharePlatform("reddit", "Reddit", "https://www.reddit.com/submit", ("reddit.com",), None),
    "facebook": SharePlatform("facebook", "Facebook", "https://www.facebook.com/", ("facebook.com",), None),
    "linkedin": SharePlatform("linkedin", "LinkedIn", "https://www.linkedin.com/feed/", ("linkedin.com",), 3000),
}

ALIASES = {"twitter": "x", "fb": "facebook", "bsky": "bluesky", "li": "linkedin"}

SUPPORTED = tuple(SHARE_PLATFORMS)


class ShareLinkError(ValueError):
    """The draft can't be turned into a share link (unsupported platform, empty or too long)."""


def normalize_platform(value: Any) -> str:
    name = str(value or "").strip().lower()
    name = ALIASES.get(name, name)
    if name in SHARE_PLATFORMS:
        return name
    if name == "instagram":
        raise ShareLinkError("Instagram isn't supported yet: it has no compose link. Supported: " + ", ".join(SUPPORTED))
    raise ShareLinkError(f"Unsupported platform {name or '(none)'!r}. Supported: {', '.join(SUPPORTED)}")


def first_link(text: str) -> str | None:
    """The first http(s) URL in `text`, without trailing punctuation."""
    match = _URL_RE.search(text or "")
    return match.group(0).rstrip(".,;:!?)]}") if match else None


def _qs(params: dict[str, str]) -> str:
    # %20 rather than "+", which compose screens don't all decode.
    return urlencode(params, quote_via=quote)


def _checked_text(text: Any) -> str:
    text = str(text or "").strip()
    if not text:
        raise ShareLinkError("The post text is empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise ShareLinkError(f"The text is {len(text)} characters; share links carry at most {MAX_TEXT_CHARS}.")
    return text


def draft_length(platform: str, text: str) -> int:
    """Length as the platform counts it (X counts each link as 23 characters)."""
    if platform != "x":
        return len(text)
    return len(_URL_RE.sub("x" * X_LINK_COST, text))


def _result(platform: SharePlatform, mode: str, url: str, text: str, note: str, **extra: Any) -> dict[str, Any]:
    length = draft_length(platform.id, text)
    over = platform.char_limit is not None and length > platform.char_limit
    warnings = []
    if over:
        warnings.append(f"{length} characters; {platform.name} allows {platform.char_limit}. Shorten it before posting.")
    return {
        "status": "handed_off",
        "published": False,  # the person still has to press Post
        "platform": platform.id,
        "platform_name": platform.name,
        "mode": mode,  # prefilled | link_only | copy_and_open
        "share_url": url,
        "copy_text": text,
        "text_preview": text[:160],
        "char_count": length,
        "char_limit": platform.char_limit,
        "over_limit": over,
        "warnings": warnings,
        "note": note,
        **extra,
    }


def build_post_link(platform_id: Any, text: Any) -> dict[str, Any]:
    """Share link for a new post. Raises ShareLinkError if it can't be built."""
    platform = SHARE_PLATFORMS[normalize_platform(platform_id)]
    text = _checked_text(text)
    link = first_link(text)

    if platform.id == "x":
        url = "https://x.com/intent/post?" + _qs({"text": text})
        return _result(platform, "prefilled", url, text, "Opens X with the post filled in. Press Post to publish.")

    if platform.id == "bluesky":
        url = "https://bsky.app/intent/compose?" + _qs({"text": text})
        return _result(platform, "prefilled", url, text, "Opens Bluesky with the post filled in. Press Post to publish.")

    if platform.id == "reddit":
        title, _, body = text.partition("\n")
        title = title.strip()[:REDDIT_TITLE_MAX]
        body = body.strip()
        if link and not body and title == link:
            params = {"type": "LINK", "url": link, "title": link}
        elif body:
            params = {"type": "TEXT", "title": title, "text": body}
        else:
            params = {"type": "TEXT", "title": title}
        url = "https://www.reddit.com/submit?" + _qs(params)
        return _result(
            platform,
            "prefilled",
            url,
            text,
            "Opens Reddit's submit page. The first line is the title. Choose a community, then press Post.",
        )

    # facebook, linkedin: the compose link carries a URL only.
    if link:
        if platform.id == "facebook":
            url = "https://www.facebook.com/sharer/sharer.php?" + _qs({"u": link})
        else:
            url = "https://www.linkedin.com/sharing/share-offsite/?" + _qs({"url": link})
        return _result(
            platform,
            "link_only",
            url,
            text,
            f"{platform.name} only takes the link from a share link. Paste the text yourself "
            "(use Copy text), then press Post.",
            link=link,
        )
    return _result(
        platform,
        "copy_and_open",
        platform.home,
        text,
        f"{platform.name} can't prefill text and the post has no link to share. Use Copy text, "
        f"open {platform.name} and paste it.",
    )


def _on_platform(platform: SharePlatform, url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme in {"http", "https"} and any(host == d or host.endswith("." + d) for d in platform.domains)


def build_reply_link(platform_id: Any, reply_to: Any, text: Any) -> dict[str, Any]:
    """Share link for a reply. `reply_to` is a post id or URL on that platform."""
    platform = SHARE_PLATFORMS[normalize_platform(platform_id)]
    text = _checked_text(text)
    target = str(reply_to or "").strip()
    on_site = target if target and _on_platform(platform, target) else None

    if platform.id == "x":
        match = _X_STATUS_RE.search(target) if on_site else None
        tweet_id = match.group(1) if match else (target if target.isdigit() else None)
        if tweet_id:
            url = "https://x.com/intent/post?" + _qs({"text": text, "in_reply_to": tweet_id})
            return _result(
                platform,
                "prefilled",
                url,
                text,
                "Opens X with the reply filled in. Press Reply to publish.",
                reply_to=tweet_id,
            )

    # No reply parameter on the other platforms: open the post and let the person paste.
    return _result(
        platform,
        "copy_and_open",
        on_site or platform.home,
        text,
        f"{platform.name} can't prefill a reply. Use Copy text, open the post and paste it as a reply.",
        reply_to=target or None,
    )


def allowed_share_hosts() -> dict[str, tuple[str, ...]]:
    """Hosts the share_url of each platform may point at (the UI checks this before opening)."""
    return {p.id: p.domains for p in SHARE_PLATFORMS.values()}
