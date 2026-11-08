"""Authorization: ask Amazon Verified Permissions (Cedar) whether a tool call is allowed.

The gateway builds an `AuthzRequest` from the token and DynamoDB, never from
tool input. `AvpAuthorizer` is the real one. `StarterPolicyAuthorizer` is a
test double that applies the same policies in plain Python.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from mandate_contracts import cedar
from mandate_contracts.roles import LOCKED_TOOLS, ROLE_GOVERNED_TOOLS, effective_role_tools, role_policy_id


# The tools the `research-tools` permit names: the ten public agency APIs plus the two procurement lookups.
RESEARCH_TOOLS = frozenset(
    {
        "census_query", "openfda_search", "eia_query", "nasa_search", "noaa_climate", "nps_parks",
        "bls_series", "fec_search", "epa_search", "fcc_search", "usaspending_search", "sam_entity_lookup",
    }
)


@dataclass(frozen=True)
class AuthzRequest:
    agent_id: str
    office: str  # the agent's office, from the SESSION item
    mode: str  # "official" or "campaign", from the SESSION item
    role: str  # the staffer's office role, from the STAFFER item; "" when none is on file
    tool: str
    resource: dict[str, Any]  # cedar.case_entity(...) or cedar.tool_entity(...)
    context: dict[str, Any]  # cedar.build_context(...): every key always present
    # The role's tool list from DynamoDB (MandateStore.get_role_tools); None = the reviewed default for `role`.
    role_tools: frozenset[str] | None = None

    @property
    def effective_role_tools(self) -> frozenset[str]:
        return effective_role_tools(self.role) if self.role_tools is None else self.role_tools


@dataclass(frozen=True)
class AuthzDecision:
    allowed: bool
    policy_ids: list[str] = field(default_factory=list)  # readable names, e.g. "forbid-bulk-export"


class Authorizer(Protocol):
    def is_authorized(self, req: AuthzRequest) -> AuthzDecision: ...


class AvpAuthorizer:
    def __init__(self, client: Any, policy_store_id: str, policy_names: dict[str, str]) -> None:
        self.client = client
        self.policy_store_id = policy_store_id
        self.policy_names = policy_names  # AVP's generated policy id -> readable name

    @classmethod
    def from_env(cls) -> "AvpAuthorizer":
        import boto3

        store_id = os.environ.get("AVP_POLICY_STORE_ID")
        if not store_id:
            raise RuntimeError("AVP_POLICY_STORE_ID is not set (run Backend/infra/setup.sh)")
        names = json.loads(os.environ.get("AVP_POLICY_NAMES") or "{}")
        client = boto3.client("verifiedpermissions", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        return cls(client, store_id, names)

    def is_authorized(self, req: AuthzRequest) -> AuthzDecision:
        response = self.client.is_authorized(
            policyStoreId=self.policy_store_id,
            principal={"entityType": f"{cedar.NAMESPACE}::Agent", "entityId": req.agent_id},
            action=cedar.action(req.tool),
            resource=req.resource["identifier"],
            context=req.context,
            entities={"entityList": [cedar.agent_entity(req.agent_id, req.office, req.mode, req.role, req.effective_role_tools), req.resource]},
        )
        ids = [self.policy_names.get(p["policyId"], p["policyId"]) for p in response.get("determiningPolicies", [])]
        # A Cedar evaluation error means some policy was skipped; a skipped forbid would
        # fail open, so any error is a deny.
        allowed = response.get("decision") == "ALLOW" and not response.get("errors")
        return AuthzDecision(allowed=allowed, policy_ids=ids)


def _attr(entity_or_context: dict[str, Any], key: str) -> Any:
    """Read a value out of AVP's typed-attribute format, e.g. {"string": "rep-x"} -> "rep-x"."""
    typed = entity_or_context[key]
    return next(iter(typed.values()))


class StarterPolicyAuthorizer:
    """Test double: the policies in packages/mandate-contracts/policies, in Python.

    Cedar semantics: any matching forbid wins; otherwise allow if a permit matches;
    otherwise deny with no determining policy.
    """

    def is_authorized(self, req: AuthzRequest) -> AuthzDecision:
        ctx = req.context["contextMap"]
        delegator_active = _attr(ctx, "delegatorActive")
        account = _attr(ctx, "account")

        forbids = []
        if req.tool == "records_bulk_export":
            forbids.append("forbid-bulk-export")
        if req.tool == "social_post" and req.mode == "official" and account == "campaign":
            forbids.append("forbid-campaign-post")
        if req.tool == "social_reply" and req.mode == "official" and account == "campaign":
            forbids.append("forbid-campaign-reply")
        locked_out = req.role not in LOCKED_TOOLS.get(req.tool, {req.role})
        if req.tool in ROLE_GOVERNED_TOOLS and (req.tool not in req.effective_role_tools or locked_out):
            forbids.append(role_policy_id(req.tool))
        if forbids:
            return AuthzDecision(False, forbids)

        permits = []
        if req.tool == "case_read" and delegator_active:
            if _attr(req.resource["attributes"], "office") == req.office:
                permits.append("case-read-same-office")
        if req.tool in ("inbox_read", "datagov_search", "web_scrape") and delegator_active:
            permits.append("read-tools")
        if req.tool == "email_send" and delegator_active and _attr(ctx, "recipientKnown"):
            permits.append("email-send-known-recipient")
        if req.tool == "social_post" and delegator_active and account == "official":
            permits.append("social-post-official")
        if req.tool == "social_reply" and delegator_active and account == "official":
            permits.append("social-reply-official")
        if req.tool in RESEARCH_TOOLS and delegator_active:
            permits.append("research-tools")
        if req.tool == "email_send_list" and delegator_active:
            permits.append("email-send-list-office")
        if req.tool == "report_save" and delegator_active:
            permits.append("report-save")
        if req.tool == "draft_revise" and delegator_active:
            permits.append("draft-revise")
        return AuthzDecision(bool(permits), permits)
