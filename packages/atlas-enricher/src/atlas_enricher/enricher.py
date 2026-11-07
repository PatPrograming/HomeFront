from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .pack import load_pack


class AnnotateEvent(str, Enum):
    allow = "allow"
    deny = "deny"
    revoke = "revoke"
    discover = "discover"
    secret_probe = "secret_probe"
    untrusted_input = "untrusted_input"


# Static gateway-event → ATLAS ID map (config, not LLM).
EVENT_MAP: dict[AnnotateEvent, dict[str, Any]] = {
    AnnotateEvent.allow: {
        "technique_ids": [],
        "mitigation_ids": ["AML.M0028", "AML.M0024"],
        "narrative": "Tool invocation permitted under AI Agent Tools Permissions Configuration.",
    },
    AnnotateEvent.deny: {
        "technique_ids": ["AML.T0053"],
        "mitigation_ids": ["AML.M0028", "AML.M0024"],
        "narrative": "Blocked unauthorized AI Agent Tool Invocation after policy deny/revoke.",
    },
    AnnotateEvent.revoke: {
        "technique_ids": ["AML.T0053"],
        "mitigation_ids": ["AML.M0028", "AML.M0024"],
        "narrative": "Grant revoked mid-task; subsequent tool calls will be denied.",
    },
    AnnotateEvent.discover: {
        "technique_ids": ["AML.T0084.001"],
        "mitigation_ids": ["AML.M0028", "AML.M0024"],
        "narrative": "Constrained discovery of AI agent tool definitions.",
    },
    AnnotateEvent.secret_probe: {
        "technique_ids": ["AML.T0098"],
        "mitigation_ids": ["AML.M0026", "AML.M0024"],
        "narrative": "Credentials remain in the gateway; agent cannot harvest tool secrets.",
    },
    AnnotateEvent.untrusted_input: {
        "technique_ids": ["AML.T0051.001"],
        "mitigation_ids": ["AML.M0030", "AML.M0033", "AML.M0024"],
        "narrative": "Untrusted scrape/social content gated before further tool chaining.",
    },
}

# Tool-specific residual risk annotations on allow.
TOOL_RESIDUAL_RISK: dict[str, dict[str, Any]] = {
    "email.send": {
        "technique_ids": ["AML.T0086"],
        "mitigation_ids": ["AML.M0028", "AML.M0029"],
        "note": "Outbound email is an exfiltration path; grant + optional HITL required.",
    },
    "social.post": {
        "technique_ids": ["AML.T0086", "AML.T0048"],
        "mitigation_ids": ["AML.M0028", "AML.M0029"],
        "note": "Social posts can cause external/reputational harm.",
    },
    "social.reply": {
        "technique_ids": ["AML.T0086", "AML.T0051.001"],
        "mitigation_ids": ["AML.M0028", "AML.M0030"],
        "note": "Replies consume untrusted social text and can exfiltrate.",
    },
    "web.scrape": {
        "technique_ids": ["AML.T0051.001"],
        "mitigation_ids": ["AML.M0030", "AML.M0033"],
        "note": "Scraped content is an indirect prompt-injection surface.",
    },
    "datagov.search": {
        "technique_ids": ["AML.T0098"],
        "mitigation_ids": ["AML.M0026"],
        "note": "api.data.gov key is held by the gateway, never returned to the agent.",
    },
}


class AnnotateRequest(BaseModel):
    event: AnnotateEvent
    tool: str | None = None
    agent_id: str | None = None
    reason: str | None = None


class AtlasRef(BaseModel):
    id: str
    name: str | None = None
    object_type: str | None = Field(default=None, alias="object_type")


class AnnotateResponse(BaseModel):
    event: AnnotateEvent
    tool: str | None = None
    agent_id: str | None = None
    technique_ids: list[str]
    mitigation_ids: list[str]
    techniques: list[dict[str, Any]]
    mitigations: list[dict[str, Any]]
    narrative: str
    residual_risk: dict[str, Any] | None = None


class AtlasEnricher:
    def __init__(self, pack: dict[str, Any] | None = None) -> None:
        self.pack = pack or load_pack()
        self.techniques: dict[str, Any] = self.pack.get("techniques") or {}
        self.mitigations: dict[str, Any] = self.pack.get("mitigations") or {}
        self.tactics: dict[str, Any] = self.pack.get("tactics") or {}
        self.relationships: dict[str, Any] = self.pack.get("relationships") or {}

    @property
    def version(self) -> str | None:
        return (self.pack.get("collection") or {}).get("version")

    def list_techniques(self, platform: str | None = "Agentic AI") -> list[dict[str, Any]]:
        out = []
        for tech in self.techniques.values():
            platforms = tech.get("platforms") or []
            if platform is None or platform in platforms:
                out.append(tech)
        return sorted(out, key=lambda t: t.get("id") or "")

    def get_technique(self, technique_id: str) -> dict[str, Any] | None:
        return self.techniques.get(technique_id)

    def get_mitigation(self, mitigation_id: str) -> dict[str, Any] | None:
        return self.mitigations.get(mitigation_id)

    def list_mitigations(self) -> list[dict[str, Any]]:
        return sorted(self.mitigations.values(), key=lambda m: m.get("id") or "")

    def mitigations_for_technique(self, technique_id: str) -> list[dict[str, Any]]:
        mitigates = self.relationships.get("mitigates") or {}
        found = []
        for mid, targets in mitigates.items():
            if technique_id in targets and mid in self.mitigations:
                found.append(self.mitigations[mid])
        return found

    def _hydrate(self, ids: list[str], store: dict[str, Any]) -> list[dict[str, Any]]:
        items = []
        for oid in ids:
            obj = store.get(oid)
            if obj:
                items.append(obj)
            else:
                items.append({"id": oid, "name": None, "missing": True})
        return items

    def annotate(self, req: AnnotateRequest) -> AnnotateResponse:
        base = EVENT_MAP[req.event]
        technique_ids = list(base["technique_ids"])
        mitigation_ids = list(base["mitigation_ids"])
        narrative = base["narrative"]
        residual: dict[str, Any] | None = None

        if req.tool and req.event == AnnotateEvent.allow:
            residual = TOOL_RESIDUAL_RISK.get(req.tool)
            if residual:
                for tid in residual.get("technique_ids") or []:
                    if tid not in technique_ids:
                        technique_ids.append(tid)
                for mid in residual.get("mitigation_ids") or []:
                    if mid not in mitigation_ids:
                        mitigation_ids.append(mid)
                narrative = f"{narrative} {residual.get('note', '')}".strip()

        if req.reason:
            narrative = f"{narrative} Reason: {req.reason}"

        return AnnotateResponse(
            event=req.event,
            tool=req.tool,
            agent_id=req.agent_id,
            technique_ids=technique_ids,
            mitigation_ids=mitigation_ids,
            techniques=self._hydrate(technique_ids, self.techniques),
            mitigations=self._hydrate(mitigation_ids, self.mitigations),
            narrative=narrative,
            residual_risk=residual,
        )


def create_enricher(pack_path: str | None = None) -> AtlasEnricher:
    if pack_path:
        return AtlasEnricher(load_pack(__import__("pathlib").Path(pack_path)))
    return AtlasEnricher()
