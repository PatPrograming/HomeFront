from __future__ import annotations

import logging
import re
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from threading import Lock
from typing import Any, Callable, Iterable, Protocol

from .approvals import ApprovalRepository, MemoryApprovalRepository
from .audit import AuditRepository, MemoryAuditRepository, compact_atlas
from .reports import MemoryReportRepository, ReportRepository

log = logging.getLogger(__name__)

# Agent ids appear in URLs; keep them short and URL-safe. Mirrors the
# check constraint on public.agents.id.
AGENT_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{2,63}$"

DEFAULT_MAX_STEPS = 10

# Fields a caller may change through update_agent(). Grants and token_version
# go through the grant methods so token invalidation stays consistent.
EDITABLE_FIELDS = frozenset({"name", "agency", "instructions", "model", "max_steps", "active"})

# Per-agent mailing list: the only addresses email.send may reach, and the
# audience of email.send_list.
MAX_RECIPIENTS = 500
_EMAIL_RE = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"'.][^@\s,;<>\"']*$")


class InvalidRecipientsError(ValueError):
    def __init__(self, invalid: list[str], message: str | None = None) -> None:
        self.invalid = invalid
        super().__init__(message or f"Invalid email addresses: {', '.join(invalid)}")


def normalize_email(address: str) -> str:
    return address.strip().lower()


def normalize_recipients(addresses: Iterable[str]) -> list[str]:
    """Trimmed, lower-cased, de-duplicated and sorted. Raises InvalidRecipientsError."""
    cleaned = {normalize_email(a) for a in addresses if a and a.strip()}
    invalid = sorted(a for a in cleaned if len(a) > 254 or not _EMAIL_RE.match(a))
    if invalid:
        raise InvalidRecipientsError(invalid)
    if len(cleaned) > MAX_RECIPIENTS:
        raise InvalidRecipientsError([], f"A mailing list holds at most {MAX_RECIPIENTS} addresses.")
    return sorted(cleaned)


# Per-agent web.scrape domain allowlist (empty = any public host).
MAX_SCRAPE_DOMAINS = 50
_DOMAIN_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_PATTERN_RE = re.compile(rf"^(?:\*\.)?{_DOMAIN_LABEL}(?:\.{_DOMAIN_LABEL})*$")


class InvalidScrapeDomainsError(ValueError):
    def __init__(self, invalid: list[str], message: str | None = None) -> None:
        self.invalid = invalid
        super().__init__(message or f"Invalid domain patterns: {', '.join(invalid)}")


def normalize_scrape_domains(patterns: Iterable[str] | None) -> list[str]:
    """Lower-cased, de-duplicated, sorted patterns: `example.gov` (exact) or
    `*.gov` (that domain and its subdomains). Raises InvalidScrapeDomainsError."""
    cleaned = {p.strip().lower().rstrip(".") for p in (patterns or ()) if p and p.strip()}
    invalid = sorted(
        p for p in cleaned if len(p) > 253 or not _DOMAIN_PATTERN_RE.match(p) or p.replace(".", "").isdigit()
    )
    if invalid:
        raise InvalidScrapeDomainsError(invalid)
    if len(cleaned) > MAX_SCRAPE_DOMAINS:
        raise InvalidScrapeDomainsError([], f"At most {MAX_SCRAPE_DOMAINS} domain patterns are allowed.")
    return sorted(cleaned)


def widens_scrape_policy(before: list[str], after: list[str]) -> bool:
    """True if `after` may allow a host `before` didn't (like adding a grant)."""
    if not before:
        return False  # was unrestricted; nothing can widen it
    return not after or bool(set(after) - set(before))


MAX_FROM_NAME = 80
MAX_SIGNATURE = 1000


class InvalidEmailSettingsError(ValueError):
    def __init__(self, field_name: str, message: str) -> None:
        self.field = field_name
        super().__init__(message)


@dataclass
class EmailSettings:
    """Who an agent's email comes from. Empty strings mean "not set"; an unset
    from_email falls back to HOMEFRONT_EMAIL_FROM at send time."""

    from_email: str = ""
    from_name: str = ""
    reply_to: str = ""
    signature: str = ""

    def view(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "EmailSettings":
        data = data or {}
        return cls(**{k: str(data.get(k) or "") for k in ("from_email", "from_name", "reply_to", "signature")})


def _valid_address(field_name: str, label: str, value: str) -> str:
    value = normalize_email(value or "")
    if value and (len(value) > 254 or not _EMAIL_RE.match(value)):
        raise InvalidEmailSettingsError(field_name, f"{label} isn't a valid email address: {value}")
    return value


def normalize_email_settings(
    from_email: str = "", from_name: str = "", reply_to: str = "", signature: str = ""
) -> EmailSettings:
    """Validated, trimmed settings. Raises InvalidEmailSettingsError naming the field."""
    name = " ".join((from_name or "").split())  # one line, no header injection
    if len(name) > MAX_FROM_NAME:
        raise InvalidEmailSettingsError("from_name", f"Sender name is limited to {MAX_FROM_NAME} characters.")
    sig = (signature or "").replace("\r\n", "\n").strip()
    if len(sig) > MAX_SIGNATURE:
        raise InvalidEmailSettingsError("signature", f"Signature is limited to {MAX_SIGNATURE} characters.")
    return EmailSettings(
        from_email=_valid_address("from_email", "Sender email", from_email),
        from_name=name,
        reply_to=_valid_address("reply_to", "Reply-to", reply_to),
        signature=sig,
    )


@dataclass
class AgentRecord:
    agent_id: str
    name: str
    agency: str
    tool_grants: set[str]
    # The agent's purpose; the runner uses it as the system prompt.
    instructions: str = ""
    # None = use the runner's default model.
    model: str | None = None
    max_steps: int = DEFAULT_MAX_STEPS
    # Cognito user id (`sub`). None = system/demo agent.
    owner_id: str | None = None
    # Agent type (see agent_types.py); sets the tool ceiling. Fixed at creation.
    kind: str = "custom"
    # Mailing list (normalized, see normalize_recipients).
    recipients: list[str] = field(default_factory=list)
    email_settings: EmailSettings = field(default_factory=EmailSettings)
    # web.scrape domain allowlist (normalized patterns). Empty = any public host.
    scrape_domains: list[str] = field(default_factory=list)
    # Social platforms this agent is for (share_links ids, e.g. ["x", "bluesky"]). Shown on its card.
    platforms: list[str] = field(default_factory=list)
    token_version: int = 1
    active: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class TokenRecord:
    token: str
    agent_id: str
    token_version: int
    # Groups this token's calls in the activity log (the run id for agent runs).
    session_id: str = field(default_factory=lambda: f"ses_{uuid.uuid4().hex[:12]}")
    issued_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 3600)


class DuplicateAgentError(Exception):
    """An agent with this id already exists."""


class AgentRepository(Protocol):
    """Persistence for agent definitions. Implementations return copies:
    mutating a returned record has no effect until it is passed to save()."""

    def list(self) -> list[AgentRecord]: ...

    def get(self, agent_id: str) -> AgentRecord | None: ...

    def insert(self, agent: AgentRecord) -> AgentRecord:
        """Raises DuplicateAgentError if the id is taken."""
        ...

    def save(self, agent: AgentRecord) -> AgentRecord: ...

    def delete(self, agent_id: str) -> None:
        """Raises KeyError if there is no such agent."""
        ...

    def count(self) -> int: ...


def _copy(agent: AgentRecord) -> AgentRecord:
    return replace(
        agent,
        tool_grants=set(agent.tool_grants),
        recipients=list(agent.recipients),
        email_settings=replace(agent.email_settings),
        scrape_domains=list(agent.scrape_domains),
        platforms=list(agent.platforms),
    )


class MemoryAgentRepository:
    """Process-local agent storage. Default for demos and tests; lost on restart."""

    backend = "memory"

    def __init__(self, seed: bool = True) -> None:
        self._agents: dict[str, AgentRecord] = {}
        if seed:
            for demo in (demo_agent(), demo_research_agent()):
                self._agents[demo.agent_id] = demo

    def list(self) -> list[AgentRecord]:
        return [_copy(a) for a in self._agents.values()]

    def get(self, agent_id: str) -> AgentRecord | None:
        agent = self._agents.get(agent_id)
        return _copy(agent) if agent else None

    def insert(self, agent: AgentRecord) -> AgentRecord:
        if agent.agent_id in self._agents:
            raise DuplicateAgentError(agent.agent_id)
        self._agents[agent.agent_id] = _copy(agent)
        return _copy(agent)

    def save(self, agent: AgentRecord) -> AgentRecord:
        if agent.agent_id not in self._agents:
            raise KeyError(agent.agent_id)
        self._agents[agent.agent_id] = _copy(agent)
        return _copy(agent)

    def delete(self, agent_id: str) -> None:
        if self._agents.pop(agent_id, None) is None:
            raise KeyError(agent_id)

    def count(self) -> int:
        return len(self._agents)


def demo_agent() -> AgentRecord:
    # Seeded into DynamoDB at startup by identity_gateway.dynamo_repo.seed_demo_agents.
    # Email-only: pair with demo_research_agent via research_first runs.
    return AgentRecord(
        agent_id="agent-comms-01",
        name="Agency Comms Agent",
        agency="Example Civic Agency",
        kind="email",
        tool_grants={"email.send", "email.send_list"},
        recipients=["constituent@example.gov"],
        max_steps=8,
        instructions=(
            "You draft and send official agency emails. When a task includes research "
            "findings from another agent, use ONLY those findings for facts and figures — "
            "never invent numbers. Cite the source year when you quote a statistic. Send "
            "only to the mailing list (or a listed address). If findings are insufficient, "
            "say so and do not send."
        ),
    )


def demo_research_agent() -> AgentRecord:
    """Shared research agent used by research→email pipelines."""
    from .agent_types import get_agent_type

    research = get_agent_type("research")
    return AgentRecord(
        agent_id="agent-research-01",
        name="Agency Research Agent",
        agency="Example Civic Agency",
        kind="research",
        tool_grants=set(research.default_tools),
        max_steps=research.default_max_steps,
        instructions=research.default_instructions,
    )


class GatewayStore:
    """Agents (via a pluggable repository), plus in-memory workload tokens and
    the ATLAS-tagged audit log."""

    def __init__(
        self, agents: AgentRepository | None = None, audit: AuditRepository | None = None
    ) -> None:
        self._lock = Lock()
        self._agents: AgentRepository = agents or MemoryAgentRepository()
        self._audit: AuditRepository = audit or MemoryAuditRepository()
        # Pending/decided human approvals for requires_hitl tools.
        self.approvals: ApprovalRepository = MemoryApprovalRepository()
        # Reports saved by agents with report.save.
        self.reports: ReportRepository = MemoryReportRepository()
        # Events the durable audit store rejected; still listed so nothing vanishes.
        self._unsaved_audit = MemoryAuditRepository()
        self.audit_write_errors = 0
        self.last_audit_error: str | None = None
        # Set by the host app when a configured store couldn't be used.
        self.storage_warnings: list[str] = []
        self.tokens: dict[str, TokenRecord] = {}
        # trace id (run id or token session) -> first untrusted read in it.
        self._tainted: dict[str, dict[str, Any]] = {}

    def use_agent_repository(self, repo: AgentRepository) -> None:
        with self._lock:
            self._agents = repo

    def use_audit_repository(self, repo: AuditRepository) -> None:
        with self._lock:
            self._audit = repo

    def use_approval_repository(self, repo: ApprovalRepository) -> None:
        with self._lock:
            self.approvals = repo

    def use_report_repository(self, repo: ReportRepository) -> None:
        with self._lock:
            self.reports = repo

    @property
    def report_backend(self) -> str:
        return getattr(self.reports, "backend", type(self.reports).__name__)

    @property
    def approval_backend(self) -> str:
        return getattr(self.approvals, "backend", type(self.approvals).__name__)

    @property
    def agent_backend(self) -> str:
        return getattr(self._agents, "backend", type(self._agents).__name__)

    @property
    def audit_backend(self) -> str:
        return getattr(self._audit, "backend", type(self._audit).__name__)

    # --- agents -----------------------------------------------------------

    def agent_count(self) -> int:
        return self._agents.count()

    def list_agents(self) -> list[AgentRecord]:
        return self._agents.list()

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        return self._agents.get(agent_id)

    def create_agent(
        self,
        name: str,
        agency: str,
        tool_grants: list[str],
        agent_id: str | None = None,
        *,
        instructions: str = "",
        model: str | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        owner_id: str | None = None,
        kind: str = "custom",
        recipients: Iterable[str] = (),
        scrape_domains: Iterable[str] = (),
        platforms: Iterable[str] = (),
    ) -> AgentRecord:
        rec = AgentRecord(
            agent_id=agent_id or f"agent-{uuid.uuid4().hex[:8]}",
            name=name,
            agency=agency,
            tool_grants=set(tool_grants),
            instructions=instructions,
            model=model,
            max_steps=max_steps,
            owner_id=owner_id,
            kind=kind,
            recipients=normalize_recipients(recipients),
            scrape_domains=normalize_scrape_domains(scrape_domains),
            platforms=list(dict.fromkeys(platforms)),
        )
        with self._lock:
            return self._agents.insert(rec)

    def update_agent(self, agent_id: str, **changes: Any) -> AgentRecord:
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"Not editable: {sorted(unknown)}")

        def apply(agent: AgentRecord) -> None:
            for key, value in changes.items():
                setattr(agent, key, value)

        return self._mutate(agent_id, apply)

    def set_recipients(self, agent_id: str, addresses: Iterable[str]) -> AgentRecord:
        recipients = normalize_recipients(addresses)

        def apply(agent: AgentRecord) -> None:
            agent.recipients = recipients

        return self._mutate(agent_id, apply)

    def set_scrape_domains(self, agent_id: str, patterns: Iterable[str]) -> AgentRecord:
        domains = normalize_scrape_domains(patterns)

        def apply(agent: AgentRecord) -> None:
            # Like grants: widening invalidates tokens; narrowing applies to the
            # next call (the policy is read fresh per call) without ending the run.
            if widens_scrape_policy(agent.scrape_domains, domains):
                agent.token_version += 1
            agent.scrape_domains = domains

        return self._mutate(agent_id, apply)

    def set_email_settings(self, agent_id: str, settings: EmailSettings) -> AgentRecord:
        def apply(agent: AgentRecord) -> None:
            agent.email_settings = replace(settings)

        return self._mutate(agent_id, apply)

    def set_grants(self, agent_id: str, grants: list[str]) -> AgentRecord:
        def apply(agent: AgentRecord) -> None:
            agent.tool_grants = set(grants)
            agent.token_version += 1

        return self._mutate(agent_id, apply)

    def grant_tool(self, agent_id: str, tool: str) -> AgentRecord:
        def apply(agent: AgentRecord) -> None:
            agent.tool_grants.add(tool)
            agent.token_version += 1

        return self._mutate(agent_id, apply)

    def revoke_tool(self, agent_id: str, tool: str) -> AgentRecord:
        def apply(agent: AgentRecord) -> None:
            # Keep token_version stable so the mid-task Bearer token still
            # authenticates; subsequent calls to this tool hit 403 AML.T0053.
            agent.tool_grants.discard(tool)

        return self._mutate(agent_id, apply)

    def delete_agent(self, agent_id: str) -> None:
        """Remove the agent. Its workload tokens stop resolving at once (the agent is
        looked up on every call). Audit rows stay, keyed by agent id."""
        with self._lock:
            self._agents.delete(agent_id)

    def revoke_all(self, agent_id: str) -> AgentRecord:
        def apply(agent: AgentRecord) -> None:
            agent.tool_grants.clear()
            agent.token_version += 1

        return self._mutate(agent_id, apply)

    def _mutate(self, agent_id: str, apply: Callable[[AgentRecord], None]) -> AgentRecord:
        # The lock makes read-modify-write atomic within this process. It does
        # not guard against a second gateway process writing the same row.
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                raise KeyError(agent_id)
            apply(agent)
            return self._agents.save(agent)

    # --- workload tokens --------------------------------------------------

    def issue_token(
        self, agent_id: str, ttl_seconds: int = 3600, session_id: str | None = None
    ) -> TokenRecord:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        if not agent.active:
            raise ValueError("Agent is inactive")
        rec = TokenRecord(
            token=f"gr_{secrets.token_urlsafe(24)}",
            agent_id=agent_id,
            token_version=agent.token_version,
            expires_at=time.time() + ttl_seconds,
        )
        if session_id:
            rec.session_id = session_id
        with self._lock:
            self.tokens[rec.token] = rec
        return rec

    def resolve_token(self, token: str) -> tuple[AgentRecord, TokenRecord] | None:
        with self._lock:
            tok = self.tokens.get(token)
        if not tok or tok.expires_at < time.time():
            return None
        # Read the agent fresh on every call so a revoke (from any process,
        # when backed by DynamoDB) applies to the very next tool call.
        agent = self._agents.get(tok.agent_id)
        if not agent or not agent.active:
            return None
        if tok.token_version != agent.token_version:
            # Mid-task revoke / grant change invalidated this token version.
            return None
        return agent, tok

    # --- taint tracking ---------------------------------------------------
    # Once a run or token session has executed an untrusted_input tool, the
    # model's context may hold injected instructions, so outbound tools are
    # refused for the rest of that trace. In memory, like the tokens it keys on.

    MAX_TAINTED = 10_000

    def mark_tainted(self, trace_id: str | None, tool: str) -> None:
        if not trace_id:
            return
        with self._lock:
            if trace_id in self._tainted:
                return
            self._tainted[trace_id] = {"tool": tool, "ts": time.time()}
            while len(self._tainted) > self.MAX_TAINTED:
                self._tainted.pop(next(iter(self._tainted)))

    def taint_of(self, trace_id: str | None) -> dict[str, Any] | None:
        """What first tainted this trace ({"tool", "ts"}), or None if it's clean."""
        if not trace_id:
            return None
        with self._lock:
            taint = self._tainted.get(trace_id)
            return dict(taint) if taint else None

    # --- audit ------------------------------------------------------------

    def append_audit(self, event: dict[str, Any]) -> dict[str, Any]:
        event = {
            **event,
            "id": f"aud_{uuid.uuid4().hex[:10]}",
            "ts": time.time(),
        }
        for key in ("atlas", "untrusted_input_atlas"):
            if key in event:
                event[key] = compact_atlas(event[key])
        try:
            self._audit.append(event)
        except Exception as exc:  # never let logging break a decision
            # Error code only (e.g. ExpiredTokenException): AWS messages can carry ARNs.
            response = getattr(exc, "response", None)
            code = (response.get("Error") or {}).get("Code") if isinstance(response, dict) else None
            reason = code or type(exc).__name__
            with self._lock:
                self.audit_write_errors += 1
                self.last_audit_error = reason
            log.warning("audit write to %s failed (%s); kept in memory", self.audit_backend, reason)
            self._unsaved_audit.append(event)
        return event

    def list_audit(
        self,
        agent_ids: Iterable[str],
        limit: int = 100,
        *,
        trace_id: str | None = None,
        decision: str | None = None,
        type_: str | None = None,
    ) -> list[dict[str, Any]]:
        agent_ids = list(agent_ids)
        filters = {"trace_id": trace_id, "decision": decision, "type_": type_}
        events = self._audit.list(agent_ids, limit, **filters)
        unsaved = self._unsaved_audit.list(agent_ids, limit, **filters)
        if unsaved:
            events = sorted(events + unsaved, key=lambda e: e.get("ts", 0), reverse=True)
        return events[:limit]
