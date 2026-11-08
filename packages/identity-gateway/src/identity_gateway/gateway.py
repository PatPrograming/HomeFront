"""`guarded()`: the one path every agent tool call takes.

    1. verify the mandate token            -> bad token: 401
    2. session active AND staffer active?  -> no: DENY  (this is how Offboard is instant)
    3. build the facts from the token and DynamoDB (never from tool input)
    4. ask Verified Permissions (Cedar)    -> DENY: stop
    5. run the tool
    6. scan untrusted output (flag only, the original text is passed on)
    7. write a decision event

Anything that goes wrong in steps 2-7 becomes a DENY. Nothing fails open.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from atlas_enricher import AnnotateRequest, AtlasEnricher
from atlas_enricher.enricher import TOOL_RESIDUAL_RISK, AnnotateEvent
from mcp_tools.handlers import invoke_tool

from mandate_contracts import TOOLS, DecisionEvent, GuardrailFlag, MandateClaims, Reason, cedar

from .authz import Authorizer, AuthzRequest
from .storage import MandateStore
from .tokens import InvalidToken, verify_mandate_token

log = logging.getLogger("identity_gateway")

Scanner = Callable[[str], GuardrailFlag]

# Mandate tool name -> the existing mcp_tools handler (and ATLAS label) it reuses.
LEGACY_TOOLS = {
    "datagov_search": "datagov.search",
    "web_scrape": "web.scrape",
    "email_send": "email.send",
    "social_post": "social.post",
}
# One address, nothing else: "a@b.gov, c@evil.com" is not one address.
SINGLE_ADDRESS = re.compile(r"^[^@\s,;<>]+@([^@\s,;<>]+)$")


def no_scan(text: str) -> GuardrailFlag:
    return GuardrailFlag()


@dataclass
class Outcome:
    event: DecisionEvent
    result: dict[str, Any] | None = None  # the tool's output; None on DENY

    @property
    def allowed(self) -> bool:
        return self.event.decision == "ALLOW"

    def deny_text(self) -> str:
        return f"DENIED ({self.event.reason}, ATLAS {', '.join(self.event.atlas)})"


class Gateway:
    def __init__(
        self,
        store: MandateStore,
        authorizer: Authorizer,
        secret: str,
        scan: Scanner = no_scan,
        enricher: AtlasEnricher | None = None,
    ) -> None:
        self.store = store
        self.authorizer = authorizer
        self.secret = secret
        self.scan = scan
        self.enricher = enricher or AtlasEnricher()

    def guarded(self, tool: str, args: dict[str, Any], token: str) -> Outcome:
        # 1. Who is calling? A forged or expired token never gets further.
        try:
            claims = verify_mandate_token(token, self.secret)
        except InvalidToken:
            # Unauthenticated callers choose `tool`; only log names we know.
            self._record(None, tool if tool in TOOLS else "unknown", None, "DENY", "invalid token")
            raise

        resource: str | None = None
        try:
            spec = TOOLS[tool]  # unknown tool -> KeyError -> DENY
            params = spec.input_model.model_validate(args)  # drops extra keys such as `office`
            resource = _resource_label(tool, params)

            # 2. Revocation check, read fresh from DynamoDB on every call.
            session = self.store.get_session(claims.sid)
            staffer = self.store.get_staffer(claims.act)
            delegator_active = bool(
                session
                and session.status == "active"
                and session.acting_for == claims.act
                and staffer
                and staffer.active
            )
            if not delegator_active:
                return self._record(claims, tool, resource, "DENY", "session revoked / delegator inactive")

            # 3. Facts for Cedar. The agent's office and mode come from the session;
            #    a case's office comes from the CASE item. Tool input only says *which* case.
            case = self.store.get_case(params.case_id) if tool == "case_read" else None
            if tool == "case_read" and case is None:
                return self._record(claims, tool, resource, "DENY", "denied by policy")
            request = AuthzRequest(
                agent_id=claims.sub,
                office=session.office,
                mode=session.mode,
                tool=tool,
                resource=cedar.case_entity(params.case_id, case.office) if case else cedar.tool_entity(tool),
                context=cedar.build_context(  # always every key; a missing key would skip a forbid
                    delegatorActive=delegator_active,
                    recipientKnown=tool == "email_send" and self._recipient_known(params.to, session.office),
                    account=params.account if tool == "social_post" else "",
                ),
            )

            # 4. Ask Verified Permissions.
            decision = self.authorizer.is_authorized(request)
            if not decision.allowed:
                return self._record(claims, tool, resource, "DENY", "denied by policy", decision.policy_ids)

            # 5. Run the tool.
            result = self._run(tool, params, case)

            # 6. Untrusted output (public inbox, web pages): flag it, never change it.
            flag = self.scan(json.dumps(result)) if spec.untrusted_output else GuardrailFlag()

            # 7. Audit, then hand the result back.
            return self._record(claims, tool, resource, "ALLOW", "allowed", decision.policy_ids, flag, result)
        except Exception:
            log.exception("guarded(%s) failed; denying", tool)
            return self._record(claims, tool, resource, "DENY", "denied by policy")

    # --- helpers ----------------------------------------------------------

    def _recipient_known(self, to: str, office: str) -> bool:
        """One address that is a .gov address or a constituent of this office's cases."""
        match = SINGLE_ADDRESS.match(to.strip())
        if not match:
            return False
        return match.group(1).lower().endswith(".gov") or to.strip().lower() in self.store.constituent_emails(office)

    def _run(self, tool: str, params: Any, case: Any) -> dict[str, Any]:
        if tool == "case_read":
            return {"id": params.case_id, **case.model_dump(mode="json")}
        if tool == "inbox_read":
            email = self.store.get_email(params.email_id)
            if email is None:
                return {"id": params.email_id, "status": "not_found"}
            return {"id": params.email_id, **email.model_dump(mode="json")}
        # records_bulk_export has no handler on purpose: even a wrong ALLOW cannot run it.
        return invoke_tool(LEGACY_TOOLS[tool], params.model_dump())

    def _atlas(self, tool: str, decision: str, flag: GuardrailFlag) -> list[str]:
        ids: list[str] = []
        if decision == "DENY":
            ids += self.enricher.annotate(AnnotateRequest(event=AnnotateEvent.deny, tool=tool)).technique_ids
            ids += TOOL_RESIDUAL_RISK.get(LEGACY_TOOLS.get(tool, ""), {}).get("technique_ids", [])
        if flag.flagged:
            ids += self.enricher.annotate(AnnotateRequest(event=AnnotateEvent.untrusted_input, tool=tool)).technique_ids
        return list(dict.fromkeys(ids))

    def _record(
        self,
        claims: MandateClaims | None,
        tool: str,
        resource: str | None,
        decision: str,
        reason: Reason,
        policy_ids: list[str] | None = None,
        flag: GuardrailFlag | None = None,
        result: dict[str, Any] | None = None,
    ) -> Outcome:
        flag = flag or GuardrailFlag()
        event = DecisionEvent(
            id=str(uuid.uuid4()),
            ts=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            sid=claims.sid if claims else "unknown",
            agent=claims.sub if claims else "unknown",
            acting_for=claims.act if claims else "unknown",
            office=claims.office if claims else "unknown",
            tool=tool,
            resource=resource,
            decision=decision,
            reason=reason,
            policy_ids=policy_ids or [],
            atlas=self._atlas(tool, decision, flag),
            guardrail=flag,
        )
        try:
            self.store.put_event(event)
        except Exception:
            # No audit record, no action: an ALLOW we cannot log is withheld.
            log.exception("could not write decision event %s", event.id)
            if decision == "ALLOW":
                event = event.model_copy(update={"decision": "DENY", "reason": "denied by policy", "atlas": self._atlas(tool, "DENY", flag)})
                return Outcome(event)
        return Outcome(event, result if decision == "ALLOW" else None)


def _resource_label(tool: str, params: Any) -> str | None:
    """What the call touches, for the audit feed (case id, email id, recipient, ...)."""
    for field_name in ("case_id", "email_id", "to", "url", "account", "query"):
        if hasattr(params, field_name):
            return getattr(params, field_name)
    return None


# --- the process-wide gateway --------------------------------------------

_gateway: Gateway | None = None


def _default_scan() -> Scanner:
    """Lane C's Bedrock Guardrails scanner when it is installed, else no scan."""
    try:
        from guardrail_scanner import scan
    except ImportError:
        return no_scan
    return scan


def get_gateway() -> Gateway:
    """The gateway built from env (DynamoDB table, AVP store, signing secret)."""
    global _gateway
    if _gateway is None:
        from .authz import AvpAuthorizer
        from .tokens import signing_secret

        _gateway = Gateway(MandateStore.from_env(), AvpAuthorizer.from_env(), signing_secret(), _default_scan())
    return _gateway


def set_gateway(gateway: Gateway | None) -> None:
    """Swap the process-wide gateway (tests inject fakes here)."""
    global _gateway
    _gateway = gateway
