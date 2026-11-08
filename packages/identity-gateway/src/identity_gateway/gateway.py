"""`guarded()`: the one path every agent tool call takes.

    1. verify the mandate token            -> bad token: 401
    2. session active AND staffer active?  -> no: DENY  (this is how Offboard is instant)
    3. build the facts from the token and DynamoDB (never from tool input)
    4. ask Verified Permissions (Cedar)    -> DENY: stop
       an outbound call the staffer has not let the agent make alone -> held as a draft, not run
    5. run the tool; a send with no Gmail (or office SES sender) or X account -> DENY "needs connection"
    6. scan untrusted output (flag only, the original text is passed on)
    7. write a decision event

Anything that goes wrong in steps 2-7 becomes a DENY. Nothing fails open.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from atlas_enricher import AnnotateRequest, AtlasEnricher
from atlas_enricher.enricher import TOOL_RESIDUAL_RISK, AnnotateEvent
from mcp_tools import handlers as report_handlers
from mcp_tools.handlers import invoke_tool
from mcp_tools import share_links
from mcp_tools.registry import get_tool

from mandate_contracts import TOOLS, DecisionEvent, GuardrailFlag, MandateClaims, Reason, cedar
from mandate_contracts.ddb import ChatMessageItem, DraftItem
from mandate_contracts.roles import role_reason

from .aauth_bridge import resolve_call_identity
from .authz import Authorizer, AuthzRequest
from .autonomy import LOCKED, MAILING_LIST, NEW_CONVERSATION, SOCIAL_CATEGORY, SOCIAL_REPLY
from .storage import MandateStore
from .tokens import InvalidToken

try:  # Lane I's Gmail/X connections; without it outbound calls behave as before
    from . import connections
except ImportError:
    connections = None

log = logging.getLogger("identity_gateway")

Scanner = Callable[[str], GuardrailFlag]

# Tools the gateway answers itself (or never runs); every other tool reuses the mcp_tools handler
# (and ATLAS label) named like it, with the first underscore as a dot: email_send_list -> email.send_list.
GATEWAY_ONLY_TOOLS = ("case_read", "inbox_read", "records_bulk_export", "draft_revise")
LEGACY_TOOLS = {name: name.replace("_", ".", 1) for name in TOOLS if name not in GATEWAY_ONLY_TOOLS}
SOCIAL_TOOLS = ("social_post", "social_reply")
# Held for review unless the staffer allowed the category.
OUTBOUND_TOOLS = ("email_send", "email_send_list", *SOCIAL_TOOLS)
# What `draft_revise` may change per draft tool. account, in_reply_to and the tool never change, and
# the mailing list is read at send time, so email_send_list gets no recipient.
REVISABLE_FIELDS = {
    "email_send": ("subject", "body", "to"),
    "email_send_list": ("subject", "body"),
    "social_post": ("text", "platform"),
    "social_reply": ("text", "platform"),
}
PLATFORMS = ("x", "bluesky", "linkedin", "facebook")
NEEDS_CONNECTION: Reason = "needs connection"
# One address, nothing else: "a@b.gov, c@evil.com" is not one address.
SINGLE_ADDRESS = re.compile(r"^[^@\s,;<>]+@([^@\s,;<>]+)$")


def no_scan(text: str) -> GuardrailFlag:
    return GuardrailFlag()


class DraftAlreadyDecided(Exception):
    """The draft was already sent or rejected."""


class ConnectionMissing(Exception):
    """The staffer has not connected the account this call sends through; `result` tells the agent to ask."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(result.get("status"))
        self.result = result


@dataclass
class Outcome:
    event: DecisionEvent
    result: dict[str, Any] | None = None  # the tool's output; None on DENY, except "needs connection"
    office_role: str | None = None  # the staffer's role, set when a role policy may have decided

    @property
    def allowed(self) -> bool:
        return self.event.decision == "ALLOW"

    def deny_text(self) -> str:
        text = f"DENIED ({self.event.reason}, ATLAS {', '.join(self.event.atlas)})"
        # Name the Cedar policy, so the agent can tell the staffer exactly which rule said no.
        if not self.event.policy_ids:
            return text
        text = f"{text} policy: {', '.join(self.event.policy_ids)}"
        # A role deny also says why in plain words, for the staffer who is not an admin.
        if any(p.startswith("role-") for p in self.event.policy_ids):
            text += f" ({role_reason(self.office_role, self.event.tool)})"
        return text


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

    def guarded(
        self,
        tool: str,
        args: dict[str, Any],
        token: str,
        *,
        headers: dict[str, str] | None = None,
        method: str = "POST",
        path: str = "/mcp",
        body: bytes | str = b"",
    ) -> Outcome:
        # 1. Who is calling? A forged or expired token never gets further.
        #    Accepts legacy HS256 mandate tokens and AAuth (ID-JAG + PoP) credentials.
        auth_protocol = "legacy_mandate"
        try:
            claims, auth_protocol = resolve_call_identity(
                bearer_token=token,
                secret=self.secret,
                headers=headers,
                method=method,
                path=path,
                body=body,
            )
        except InvalidToken:
            # Unauthenticated callers choose `tool`; only log names we know.
            self._record(None, tool if tool in TOOLS else "unknown", None, "DENY", "invalid token")
            raise

        resource: str | None = None
        stage = "checks"  # which step failed, if one does: names the real cause in the DENY event
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
                return self._record(
                    claims, tool, resource, "DENY", "session revoked / delegator inactive", auth_protocol=auth_protocol
                )

            # 2b. Task scope. Once the staffer approves a plan, the session may use only that plan's tools,
            #     whatever else the role allows. Checked before Cedar; it only narrows, never widens.
            plan_tools = self.store.plan_tools(claims.sid)
            # Revising its own held draft is part of any plan that writes one ("make it shorter").
            revising_plan_draft = tool == "draft_revise" and plan_tools is not None and any(t in plan_tools for t in OUTBOUND_TOOLS)
            if plan_tools is not None and tool not in plan_tools and not revising_plan_draft:
                return self._record(
                    claims, tool, resource, "DENY", "not in the approved plan", auth_protocol=auth_protocol
                )

            # 3. Facts for Cedar. The agent's office and mode come from the session;
            #    a case's office comes from the CASE item. Tool input only says *which* case.
            case = self.store.get_case(params.case_id) if tool == "case_read" else None
            if tool == "case_read" and case is None:
                return self._record(
                    claims, tool, resource, "DENY", "denied by policy", auth_protocol=auth_protocol
                )
            request = AuthzRequest(
                agent_id=claims.sub,
                office=session.office,
                mode=session.mode,
                role=staffer.office_role or "",  # from the STAFFER item, never from the token or input
                role_tools=self.store.get_role_tools(staffer.office_role),  # the role's list, read fresh
                tool=tool,
                resource=cedar.case_entity(params.case_id, case.office) if case else cedar.tool_entity(tool),
                context=cedar.build_context(  # always every key; a missing key would skip a forbid
                    delegatorActive=delegator_active,
                    recipientKnown=tool == "email_send" and self._recipient_known(params.to, session.office),
                    account=params.account if tool in SOCIAL_TOOLS else "",
                ),
            )

            # 4. Ask Verified Permissions.
            stage = "permission"
            decision = self.authorizer.is_authorized(request)
            stage = "tool"
            if not decision.allowed:
                denied = self._record(
                    claims,
                    tool,
                    resource,
                    "DENY",
                    "denied by policy",
                    decision.policy_ids,
                    auth_protocol=auth_protocol,
                )
                denied.office_role = staffer.office_role
                return denied

            if tool == "draft_revise":  # edits a held draft; sends nothing, so no hold and no connection check
                return self._revise_draft(claims, resource, params, decision.policy_ids)

            # 4b. Earned autonomy. Cedar says the agent *may* send this; the staffer decides whether it
            #     may send it without review. Until then the gateway keeps a draft and sends nothing.
            if tool in OUTBOUND_TOOLS:
                category = self._outbound_category(tool, params)
                # A locked category is held even if its stored level says "auto" or a grant exists.
                # Otherwise it runs if the staffer set it to "auto", or granted it for this session.
                allowed_alone = (
                    self.store.get_autonomy(claims.act, category).level == "auto"
                    or self.store.has_grant(claims.sid, category)
                )
                if category in LOCKED or not allowed_alone:
                    held = self._hold(claims, tool, category, params)
                    return self._record(
                        claims,
                        tool,
                        resource,
                        "ALLOW",
                        "held for staff review",
                        decision.policy_ids,
                        result=held,
                        auth_protocol=auth_protocol,
                    )

            # 5. Run the tool.
            try:
                result = self._run(tool, params, case, claims)
            except ConnectionMissing as missing:  # nothing to send through; no draft is kept
                return self._deny_needs_connection(claims, tool, resource, missing)

            # 6. Untrusted output (public inbox, web pages): flag it, never change it.
            flag = self.scan(json.dumps(result)) if spec.untrusted_output else GuardrailFlag()

            # 7. Audit, then hand the result back.
            return self._record(
                claims,
                tool,
                resource,
                "ALLOW",
                "allowed",
                decision.policy_ids,
                flag,
                result,
                auth_protocol=auth_protocol,
            )
        except Exception as error:
            log.exception("guarded(%s) failed; denying", tool)
            return self._record(claims, tool, resource, "DENY", failure_reason(error, stage), auth_protocol=auth_protocol)

    def send_draft(self, draft_id: str, draft: DraftItem, text: dict[str, str]) -> Outcome:
        """The staffer approved a held draft, maybe with new wording. Check it again, then send it.

        The staffer is the one sending, so the check is the staffer's own standing (active, and
        Cedar's rules for the recipient or account), not the agent session's. Raises
        DraftAlreadyDecided when another approval or a rejection got there first.
        """
        tool = draft.tool
        params = TOOLS[tool].input_model.model_validate({**draft.args, **text})
        resource = _resource_label(tool, params)
        now = int(time.time())
        # Who the event is about: the agent that drafted it, acting for the staffer who approved it.
        claims = MandateClaims(
            sub=draft.agent, act=draft.acting_for, office=draft.office, mode="official", sid=draft.sid, iat=now, exp=now
        )
        staffer = self.store.get_staffer(draft.acting_for)
        if staffer is None or not staffer.active:
            return self._record(claims, tool, resource, "DENY", "session revoked / delegator inactive")
        decision = self.authorizer.is_authorized(
            AuthzRequest(
                agent_id=draft.agent,
                office=draft.office,
                mode="official",
                role=staffer.office_role or "",
                role_tools=self.store.get_role_tools(staffer.office_role),
                tool=tool,
                resource=cedar.tool_entity(tool),
                context=cedar.build_context(
                    delegatorActive=True,
                    recipientKnown=tool == "email_send" and self._recipient_known(params.to, draft.office),
                    account=params.account if tool in SOCIAL_TOOLS else "",
                ),
            )
        )
        if not decision.allowed:
            denied = self._record(claims, tool, resource, "DENY", "denied by policy", decision.policy_ids)
            denied.office_role = staffer.office_role
            return denied

        sent = draft.model_copy(
            update={
                "status": "sent",
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "edited": draft.edited or any(draft.args.get(k) != v for k, v in text.items()),
            }
        )
        # Marked sent before it runs, so a double click can never send it twice.
        if not self.store.decide_draft(draft_id, sent):
            raise DraftAlreadyDecided(draft_id)
        try:
            result = self._run(tool, params, None, claims)
        except ConnectionMissing as missing:
            if tool == "social_post" and getattr(params, "platform", "x") == "x":
                # No X account connected: hand off to X's own compose page. The draft stays "sent"
                # (handed off); the staffer presses Post on X themselves.
                link = share_links.build_post_link("x", params.text)
                result = {"status": "share_link", "url": link["share_url"], "handoff": True}
                return self._record(claims, tool, resource, "ALLOW", "handed off to X", decision.policy_ids, result=result)
            self.store.put_draft(draft_id, draft)
            return self._deny_needs_connection(claims, tool, resource, missing)
        except Exception:
            log.exception("sending draft %s failed; it stays in the review queue", draft_id)
            self.store.put_draft(draft_id, draft)
            return self._record(claims, tool, resource, "DENY", "denied by policy")
        return self._record(claims, tool, resource, "ALLOW", "approved by staff", decision.policy_ids, result=result)

    # --- helpers ----------------------------------------------------------

    def _outbound_category(self, tool: str, params: Any) -> str:
        """From DynamoDB, never from the agent: the kind of the email this replies to."""
        if tool == "social_post":
            return SOCIAL_CATEGORY
        if tool == "social_reply":
            return SOCIAL_REPLY
        if tool == "email_send_list":
            return MAILING_LIST
        email = self.store.get_email(params.in_reply_to) if params.in_reply_to else None
        if email is None or email.from_.strip().lower() != params.to.strip().lower():
            return NEW_CONVERSATION
        return email.kind

    def _hold(self, claims: MandateClaims, tool: str, category: str, params: Any) -> dict[str, Any]:
        draft_id = str(uuid.uuid4())
        self.store.put_draft(
            draft_id,
            DraftItem(
                status="pending",
                sid=claims.sid,
                agent=claims.sub,
                acting_for=claims.act,
                office=claims.office,
                tool=tool,
                category=category,
                args=params.model_dump(exclude_none=True),
                created_at=datetime.now(timezone.utc).isoformat(),
            ),
        )
        return {
            "status": "held_for_review",
            "draftId": draft_id,
            "category": category,
            "note": f"NOT sent. Saved as a draft for {claims.act} to review in the console.",
        }

    def _revise_draft(self, claims: MandateClaims, resource: str | None, params: Any, policy_ids: list[str]) -> Outcome:
        """Apply the agent's edits to its own session's pending draft, in place."""
        draft_id = params.draft_id
        draft = self.store.get_draft(draft_id)
        if draft is None or draft.status != "pending" or draft.sid != claims.sid:
            return self._record(claims, "draft_revise", resource, "DENY", "denied by policy")
        changes = {k: v for k in REVISABLE_FIELDS[draft.tool] if (v := getattr(params, k)) is not None}
        if changes.get("platform", "x") not in PLATFORMS:
            return self._record(claims, "draft_revise", resource, "DENY", "denied by policy")
        revised = draft.model_copy(
            update={"args": {**draft.args, **changes}, "version": draft.version + 1, "revising": False}
        )
        # Written only while still pending, so a revision can never undo an approval or rejection that just happened.
        if not self.store.decide_draft(draft_id, revised):
            return self._record(claims, "draft_revise", resource, "DENY", "denied by policy")
        result = {"status": "revised", "draftId": draft_id, "version": revised.version, "args": revised.args}
        return self._record(claims, "draft_revise", resource, "ALLOW", "allowed", policy_ids, result=result)

    def _deny_needs_connection(
        self, claims: MandateClaims, tool: str, resource: str | None, missing: ConnectionMissing
    ) -> Outcome:
        """Audit the deny and tell the staffer in the chat, so it shows without the agent repeating it."""
        self.store.put_chat_message(
            claims.sid,
            ChatMessageItem(
                id=str(uuid.uuid4()),
                ts=datetime.now(timezone.utc).isoformat(),
                role="assistant",
                text=f"I couldn't send this: {claims.act} hasn't connected {missing.result['label']}. Nothing was sent.",
                connect=missing.result["provider"],
            ),
        )
        return self._record(claims, tool, resource, "DENY", NEEDS_CONNECTION, result=missing.result)

    def _recipient_known(self, to: str, office: str) -> bool:
        """One address that is a .gov address or a constituent of this office's cases."""
        match = SINGLE_ADDRESS.match(to.strip())
        if not match:
            return False
        return match.group(1).lower().endswith(".gov") or to.strip().lower() in self.store.constituent_emails(office)

    def _send_connected(self, tool: str, params: Any, claims: MandateClaims) -> dict[str, Any]:
        """Send through the staffer's own Gmail or X account (the caller already found it connected)."""
        try:
            if tool == "email_send":
                return connections.send_gmail(claims.act, params.to, params.subject, params.body, in_reply_to=params.in_reply_to)
            if tool == "email_send_list":
                recipients = sorted(self.store.constituent_emails(claims.office))
                for sent, to in enumerate(recipients):
                    try:
                        connections.send_gmail(claims.act, to, params.subject, params.body)
                    except connections.ProviderError:
                        if sent == 0:
                            raise
                        # Some people already got it, so the draft must not go back to pending: a retry would
                        # email them twice. Report who is left instead.
                        failed = recipients[sent:]
                        return {"status": "partial_failure", "provider": "gmail", "sent": sent, "failed": failed}
                return {"status": "sent", "provider": "gmail", "sent": len(recipients)}
            reply_to = params.reply_to if tool == "social_reply" else None
            return connections.post_x(claims.act, params.text, reply_to=reply_to)
        except connections.NeedsConnection as exc:  # e.g. the token expired between the check and the send
            raise ConnectionMissing(connections.needs_connection_result(exc)) from exc

    def _run(self, tool: str, params: Any, case: Any, claims: MandateClaims) -> dict[str, Any]:
        if tool == "case_read":
            return {"id": params.case_id, **case.model_dump(mode="json")}
        if tool == "inbox_read":
            email = self.store.get_email(params.email_id)
            if email is None:
                return {"id": params.email_id, "status": "not_found"}
            return {"id": params.email_id, **email.model_dump(mode="json")}
        provider = connections.provider_for_tool(tool, params.model_dump()) if connections is not None else None
        if provider:
            try:
                connections.require_connection(claims.act, provider)
            except connections.NeedsConnection as exc:
                # Email without Gmail falls through to the existing SES handler when the office has a sender.
                if not (provider == "gmail" and report_handlers.resolve_sender(None)["from_email"]):
                    raise ConnectionMissing(connections.needs_connection_result(exc)) from exc
            else:
                return self._send_connected(tool, params, claims)
        # records_bulk_export has no handler on purpose: even a wrong ALLOW cannot run it.
        handler_args = params.model_dump(by_alias=True)  # the agency APIs read `for`, `in` and `parkCode`
        if tool == "email_send_list":  # the list is read here, at send time, never from input
            handler_args["recipients"] = sorted(self.store.constituent_emails(claims.office))
        if tool == "report_save":  # who saved it comes from the token, never from input
            # The standalone gateway never loads agents_api, which owns the report store. Register it here
            # unless a sink is already set. Lazy because agents_api imports this module.
            if report_handlers._report_sink is None:
                from .agents_api import _save_report

                report_handlers.set_report_sink(_save_report)
            handler_args |= {"agent_id": f"assistant-{claims.act}", "trace_id": claims.sid, "run_id": None}
        result = invoke_tool(LEGACY_TOOLS[tool], handler_args)
        registered = get_tool(LEGACY_TOOLS[tool])
        if registered and registered.holds_secrets:  # same scrub as agents_api._run_handler
            result = {**result, "credentials_exposed": False}
            result.pop("api_key", None)
            result.pop("X-Api-Key", None)
        return result

    def _atlas(self, tool: str, decision: str, reason: Reason, flag: GuardrailFlag) -> list[str]:
        ids: list[str] = []
        if decision == "DENY" and reason != NEEDS_CONNECTION:  # a missing account is not an attack
            ids += self.enricher.annotate(AnnotateRequest(event=AnnotateEvent.deny, tool=tool)).technique_ids
            ids += self._tool_risk_techniques(LEGACY_TOOLS.get(tool, ""))
        if flag.flagged:
            ids += self.enricher.annotate(AnnotateRequest(event=AnnotateEvent.untrusted_input, tool=tool)).technique_ids
        return list(dict.fromkeys(ids))

    @staticmethod
    def _tool_risk_techniques(dotted: str) -> list[str]:
        """The enricher's residual-risk labels, else the tool registry's own (new tools have no residual entry)."""
        residual = TOOL_RESIDUAL_RISK.get(dotted)
        if residual:
            return residual.get("technique_ids", [])
        registered = get_tool(dotted)
        return registered.atlas_techniques if registered else []

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
        auth_protocol: str | None = None,
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
            atlas=self._atlas(tool, decision, reason, flag),
            guardrail=flag,
            auth_protocol=auth_protocol,
        )
        try:
            self.store.put_event(event)
        except Exception:
            # No audit record, no action: an ALLOW we cannot log is withheld.
            log.exception("could not write decision event %s", event.id)
            if decision == "ALLOW":
                event = event.model_copy(update={"decision": "DENY", "reason": "denied by policy", "atlas": self._atlas(tool, "DENY", "denied by policy", flag)})
                return Outcome(event)
        return Outcome(event, result if decision == "ALLOW" or reason == NEEDS_CONNECTION else None)


EXPIRED_CODES = {"ExpiredToken", "ExpiredTokenException", "UnrecognizedClientException", "InvalidClientTokenId",
                 "RequestExpired", "InvalidSignatureException"}
UNAVAILABLE_CODES = {"ThrottlingException", "ServiceUnavailableException", "InternalServerException",
                     "RequestTimeout", "TooManyRequestsException"}


def failure_reason(error: Exception, stage: str) -> Reason:
    """Why a check could not run, for the DENY event. Fail closed either way; only the label differs.

    A Cedar "no" is recorded where it happens ("denied by policy"); this is for exceptions, which used
    to be recorded as "denied by policy" too, so expired AWS credentials looked like a forbid.
    """
    chain, seen = [], error
    while seen is not None and len(chain) < 5:
        chain.append(seen)
        seen = seen.__cause__ or seen.__context__
    for e in chain:
        name = type(e).__name__
        response = getattr(e, "response", None)
        code = response.get("Error", {}).get("Code", "") if isinstance(response, dict) else ""
        if name in {"NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError"} or code in EXPIRED_CODES:
            return "AWS credentials expired on the server"
        if code in {"AccessDeniedException", "AccessDenied", "UnauthorizedOperation"}:
            return "server lacks AWS permission"
        if stage == "permission" and (
            code in UNAVAILABLE_CODES or isinstance(e, TimeoutError) or name in {"EndpointConnectionError",
                                                                                  "ConnectTimeoutError", "ReadTimeoutError"}
        ):
            return "permission service unavailable"
    if stage == "checks" and isinstance(error, (KeyError, ValueError)):
        return "denied by policy"  # an unknown tool or invalid input: the caller's request, not the server
    return "server error (failed closed)"


def _resource_label(tool: str, params: Any) -> str | None:
    """What the call touches, for the audit feed (case id, email id, recipient, ...)."""
    for field_name in ("case_id", "email_id", "draft_id", "to", "url", "account", "query"):
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
