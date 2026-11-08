"""Multi-agent pipelines.

research_then_email: a research agent finds datasets/figures, then an email
agent uses only those findings to draft/send (still subject to mailing list +
HITL approval).
"""

from __future__ import annotations

import logging
from typing import Any

from .runner import AgentRunner, RunRecord, new_run
from .store import AgentRecord

log = logging.getLogger(__name__)

RESEARCH_TASK = """Research the following request for a colleague who will email the result.

Find official datasets and/or .gov pages, extract the specific figure(s) or facts needed,
and cite sources with years. If you cannot verify a number, say what is missing — do not guess.

Request:
{task}
"""

EMAIL_TASK = """Complete the original request using ONLY the research findings below.
Cite figures with their year/source. Do not invent numbers. If findings are insufficient,
explain the gap and do not send email.

Original request:
{task}

Research findings:
{findings}
"""


def _merge_usage(into: RunRecord, frm: RunRecord) -> None:
    into.usage["input_tokens"] += frm.usage.get("input_tokens", 0) or 0
    into.usage["output_tokens"] += frm.usage.get("output_tokens", 0) or 0
    into.model_turns += frm.model_turns


def _tag_steps(steps: list[dict[str, Any]], *, agent_id: str, stage: str) -> list[dict[str, Any]]:
    out = []
    for step in steps:
        tagged = dict(step)
        tagged["pipeline_agent"] = agent_id
        tagged["pipeline_stage"] = stage
        out.append(tagged)
    return out


def execute_research_then_email(
    active_runner: AgentRunner,
    email_run: RunRecord,
    email_agent: AgentRecord,
    research_agent: AgentRecord,
    *,
    add_run,
) -> RunRecord:
    """Fill `email_run` by running research first, then the email agent.

    `add_run` persists the research run so it can be polled separately.
    """
    original_task = email_run.task
    email_run.pipeline = "research_then_email"
    email_run.research_agent_id = research_agent.agent_id
    email_run.status = "running"
    email_run.started_at = email_run.started_at or email_run.created_at

    email_run.steps.append(
        {
            "type": "pipeline",
            "stage": "research",
            "agent_id": research_agent.agent_id,
            "message": f"Research agent ({research_agent.name}) is finding datasets and figures…",
        }
    )

    research_run = new_run(
        research_agent,
        RESEARCH_TASK.format(task=original_task),
        active_runner.model_for(research_agent),
        email_run.created_by,
    )
    add_run(research_run)
    email_run.research_run_id = research_run.run_id
    active_runner.execute(research_run, research_agent)

    email_run.steps.extend(
        _tag_steps(research_run.steps, agent_id=research_agent.agent_id, stage="research")
    )
    _merge_usage(email_run, research_run)
    email_run.research_output = research_run.output
    email_run.steps.append(
        {
            "type": "pipeline",
            "stage": "handoff",
            "research_run_id": research_run.run_id,
            "research_status": research_run.status,
            "message": (
                "Research finished; handing findings to the email agent…"
                if research_run.output
                else "Research finished without a usable summary."
            ),
            "result_preview": (research_run.output or research_run.error or "")[:400],
        }
    )

    findings = (research_run.output or "").strip()
    if research_run.status not in ("completed", "max_steps") or not findings:
        email_run.status = "failed"
        email_run.error = (
            f"Research agent did not produce findings "
            f"({research_run.status}): {research_run.error or 'empty summary'}"
        )
        email_run.finished_at = research_run.finished_at
        return email_run

    email_run.steps.append(
        {
            "type": "pipeline",
            "stage": "email",
            "agent_id": email_agent.agent_id,
            "message": f"Email agent ({email_agent.name}) is drafting from research findings…",
        }
    )

    # Run email phase on a child record so the runner's status/task lifecycle
    # doesn't clobber the pipeline parent mid-flight; then fold results back.
    email_phase = new_run(
        email_agent,
        EMAIL_TASK.format(task=original_task, findings=findings),
        active_runner.model_for(email_agent),
        email_run.created_by,
    )
    active_runner.execute(email_phase, email_agent)

    email_run.steps.extend(
        _tag_steps(email_phase.steps, agent_id=email_agent.agent_id, stage="email")
    )
    _merge_usage(email_run, email_phase)
    email_run.status = email_phase.status
    email_run.error = email_phase.error
    email_run.finished_at = email_phase.finished_at
    email_run.output = _combined_output(findings, email_phase.output)
    email_run.task = original_task
    return email_run


def _combined_output(findings: str, email_output: str | None) -> str:
    parts = ["## Research findings", findings.strip()]
    if email_output and email_output.strip():
        parts.extend(["", "## Email agent", email_output.strip()])
    return "\n".join(parts)
