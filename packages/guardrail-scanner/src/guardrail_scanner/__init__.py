"""Best-effort prompt-injection detector for untrusted tool output (brief §3, WP5).

The gateway calls `scan(text)` on the results of tools whose output is untrusted
(`inbox_read`, `web_scrape`). It asks Bedrock `ApplyGuardrail` (source INPUT)
whether the text looks like a prompt attack, and returns only a flag and the
categories that fired. It never returns or substitutes the masked text: the
caller passes the original text on and logs the flag in the decision event.

This is a detector, not a control. It can miss attacks (encoded or split
across emails), so it never fails closed: on any error it returns
`flagged=False, categories=["SCAN_ERROR"]`. Cedar in the gateway is the guarantee.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from mandate_contracts import GuardrailFlag

log = logging.getLogger(__name__)

_client = None


def _bedrock_runtime():
    global _client
    if _client is None:
        import boto3

        _client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _client


def _categories(response: dict[str, Any]) -> tuple[bool, list[str]]:
    """Attack filters (content, topic, word) set `flagged`. PII is listed but is not an attack."""
    flagged, found = False, []
    for assessment in response.get("assessments", []):
        hits = [
            *assessment.get("contentPolicy", {}).get("filters", []),
            *assessment.get("topicPolicy", {}).get("topics", []),
            *assessment.get("wordPolicy", {}).get("customWords", []),
        ]
        for hit in hits:
            if hit.get("detected", hit.get("action") != "NONE"):
                flagged = True
                found.append(hit.get("type") or hit.get("name") or hit.get("match"))
        for pii in assessment.get("sensitiveInformationPolicy", {}).get("piiEntities", []):
            if pii.get("detected", pii.get("action") != "NONE"):
                found.append(f"PII:{pii['type']}")
    return flagged, sorted(set(found))


def scan(text: str, client: Any = None) -> GuardrailFlag:
    """Flag `text` with ApplyGuardrail. Never raises; never returns the masked text."""
    try:
        response = (client or _bedrock_runtime()).apply_guardrail(
            guardrailIdentifier=os.environ["GUARDRAIL_ID"],
            guardrailVersion=os.environ["GUARDRAIL_VERSION"],
            source="INPUT",
            content=[{"text": {"text": text}}],
        )
        flagged, categories = _categories(response)
        return GuardrailFlag(flagged=flagged, categories=categories)
    except Exception:  # best-effort detector: any failure is logged and reported as SCAN_ERROR
        log.exception("guardrail scan failed")
        return GuardrailFlag(flagged=False, categories=["SCAN_ERROR"])


__all__ = ["scan"]
