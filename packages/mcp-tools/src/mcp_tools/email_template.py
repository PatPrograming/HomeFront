"""The branded template every outbound agent email is sent in.

Email HTML is its own dialect: table layout, inline styles, no web fonts, no
CSS the major clients (Gmail, Outlook desktop, Apple Mail) strip. Everything
the agent or a reviewer wrote is escaped; only URLs become links.
"""

from __future__ import annotations

import html
import re
from typing import Any

BRAND = "#112e51"  # dark federal blue: header rule and links
TEXT = "#1b1b1b"
MUTED = "#565c65"
RULE = "#dfe1e2"
PAGE = "#f0f0f0"

LOGO_MAX_HEIGHT = 64
LOGO_MAX_WIDTH = 240

_URL = re.compile(r"https?://[^\s<>\"']+")
_TRAILING = ".,;:!?)]}'\""


def _linkify(escaped: str) -> str:
    def link(m: re.Match[str]) -> str:
        url = m.group(0)
        tail = ""
        while url and url[-1] in _TRAILING:
            tail = url[-1] + tail
            url = url[:-1]
        return f'<a href="{url}" style="color:{BRAND};text-decoration:underline;">{url}</a>{tail}'

    return _URL.sub(link, escaped)


def _paragraphs(text: str, style: str) -> str:
    blocks = [b.strip("\n") for b in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if b.strip()]
    return "".join(
        f'<p style="{style}">{_linkify(html.escape(b)).replace(chr(10), "<br>")}</p>' for b in blocks
    )


def logo_box(width: int, height: int) -> tuple[int, int]:
    """Display size: at most LOGO_MAX_HEIGHT tall and LOGO_MAX_WIDTH wide, never upscaled."""
    scale = min(1.0, LOGO_MAX_HEIGHT / height, LOGO_MAX_WIDTH / width)
    return max(1, round(width * scale)), max(1, round(height * scale))


def org_name(sender: dict[str, Any]) -> str:
    return (sender.get("agency") or sender.get("from_name") or "").strip()


def footer_lines(sender: dict[str, Any], bulk: bool) -> list[str]:
    org = org_name(sender) or "this office"
    reply = sender.get("reply_to") or sender.get("from_email") or ""
    lines = [f"This is an official message from {org}."]
    if reply:
        lines.append(f"Questions? Reply to this email or write to {reply}.")
    if bulk:
        lines.append(
            f"You're receiving this because you're on {org}'s mailing list."
            + (f" To stop receiving these emails, reply with \"unsubscribe\" or write to {reply}." if reply else "")
        )
    return lines


def render_text(body: str, signature: str, sender: dict[str, Any], bulk: bool = False) -> str:
    parts = [body.rstrip()]
    if signature:
        parts.append(signature)
    parts.append("--\n" + "\n".join(footer_lines(sender, bulk)))
    return "\n\n".join(parts) + "\n"


def render_html(
    subject: str,
    body: str,
    signature: str,
    sender: dict[str, Any],
    *,
    logo_src: str | None = None,
    logo_size: tuple[int, int] | None = None,
    bulk: bool = False,
) -> str:
    org = org_name(sender)
    esc_org = html.escape(org)
    font = "font-family:Georgia,'Times New Roman',serif;"
    sans = "font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"

    header_cells = ""
    if logo_src:
        w, h = logo_box(*(logo_size or (LOGO_MAX_HEIGHT, LOGO_MAX_HEIGHT)))
        header_cells += (
            f'<td valign="middle" style="padding:0 16px 0 0;">'
            f'<img src="{html.escape(logo_src)}" width="{w}" height="{h}" alt="{esc_org or "Logo"}" '
            f'style="display:block;border:0;outline:none;width:{w}px;height:{h}px;"></td>'
        )
    if org:
        header_cells += (
            f'<td valign="middle" style="{font}font-size:20px;line-height:1.3;font-weight:bold;color:{BRAND};">'
            f"{esc_org}</td>"
        )
    header = (
        f'<tr><td style="padding:24px 32px 20px 32px;border-bottom:1px solid {RULE};">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>{header_cells}</tr></table>'
        f"</td></tr>"
        if header_cells
        else ""
    )

    body_style = f"margin:0 0 16px 0;{sans}font-size:16px;line-height:1.6;color:{TEXT};"
    sig_style = f"margin:0 0 4px 0;{sans}font-size:15px;line-height:1.5;color:{MUTED};"
    signature_html = (
        f'<tr><td style="padding:0 32px 28px 32px;">'
        f'<div style="border-top:1px solid {RULE};padding-top:16px;">{_paragraphs(signature, sig_style)}</div>'
        f"</td></tr>"
        if signature
        else ""
    )
    foot_style = f"margin:0 0 6px 0;{sans}font-size:12px;line-height:1.5;color:{MUTED};"
    footer = "".join(f'<p style="{foot_style}">{_linkify(html.escape(line))}</p>' for line in footer_lines(sender, bulk))
    preheader = html.escape(" ".join(body.split())[:140])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light">
<title>{html.escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:{PAGE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAGE};">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background:#ffffff;border-top:4px solid {BRAND};">
{header}
<tr><td style="padding:28px 32px 8px 32px;">
<h1 style="margin:0 0 20px 0;{font}font-size:24px;line-height:1.3;font-weight:bold;color:{TEXT};">{html.escape(subject)}</h1>
{_paragraphs(body, body_style)}
</td></tr>
{signature_html}
</table>
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;">
<tr><td style="padding:16px 32px;">{footer}</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""
