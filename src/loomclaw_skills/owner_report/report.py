from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from loomclaw_skills.shared.persona.state import PersonaStateStore
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.schemas.report import OwnerReport, ReportResult


TIMESTAMP_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})T")
ACTIVITY_RE = re.compile(r"^- \[(?P<timestamp>[^\]]+)\] (?P<event>.+)$")


def generate_owner_report(runtime_home: Path, *, today: date | None = None) -> ReportResult:
    report_date = today or date.today()
    state = RuntimeStateStore(runtime_home / "runtime-state.json").load()
    if state is None:
        raise RuntimeError("runtime-state.json is missing")

    persona = PersonaStateStore(runtime_home / "persona-memory.json").load()
    conversation_files = sorted(path.name for path in (runtime_home / "conversations").glob("*.md"))
    summary = OwnerReport(
        sent_friend_requests=count_activity(runtime_home, report_date, prefix="sent friend request"),
        accepted_friend_requests=count_activity(runtime_home, report_date, prefix="accepted friend request"),
        pending_friend_requests=len([value for value in state.relationship_cache.values() if value == "request_pending"]),
        mailbox_messages_today=count_mailbox_messages(runtime_home, report_date),
        persona_last_refined_at=persona.last_refined_at if persona is not None else None,
        latest_refinement_source=persona.last_refinement_source if persona is not None else None,
        significant_persona_change_today=is_significant_change_today(persona, report_date),
        persona_open_questions=persona.open_questions if persona is not None else [],
        relationship_cache=dict(state.relationship_cache),
        conversation_files=conversation_files,
    )
    output = runtime_home / "reports" / f"daily-report-{report_date.isoformat()}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_owner_report(summary, report_date=report_date))
    return ReportResult(path=output)


def count_mailbox_messages(runtime_home: Path, report_date: date) -> int:
    conversations_dir = runtime_home / "conversations"
    if not conversations_dir.exists():
        return 0

    count = 0
    prefix = report_date.isoformat()
    for path in conversations_dir.glob("*.md"):
        for line in path.read_text().splitlines():
            match = TIMESTAMP_RE.match(line)
            if match and match.group(1) == prefix:
                count += 1
    return count


def count_activity(runtime_home: Path, report_date: date, *, prefix: str) -> int:
    activity_log = runtime_home / "activity-log.md"
    if not activity_log.exists():
        return 0

    count = 0
    for line in activity_log.read_text().splitlines():
        match = ACTIVITY_RE.match(line)
        if not match:
            continue
        if not match.group("timestamp").startswith(report_date.isoformat()):
            continue
        if match.group("event").startswith(prefix):
            count += 1
    return count


def is_significant_change_today(persona, report_date: date) -> bool:
    if persona is None or persona.last_significant_change_at is None:
        return False
    return persona.last_significant_change_at.startswith(report_date.isoformat())


def render_owner_report(summary: OwnerReport, *, report_date: date) -> str:
    relationship_lines = [
        f"- {agent_id}: {state}"
        for agent_id, state in sorted(summary.relationship_cache.items())
    ] or ["- none"]
    conversation_lines = [f"- {name}" for name in summary.conversation_files] or ["- none"]
    open_question_lines = [f"- {item}" for item in summary.persona_open_questions] or ["- none"]

    lines = [
        f"# LoomClaw Daily Report - {report_date.isoformat()}",
        "",
        "## Friend Requests",
        f"- Sent friend requests: {summary.sent_friend_requests}",
        f"- Accepted friend requests: {summary.accepted_friend_requests}",
        f"- Pending friend requests: {summary.pending_friend_requests}",
        "",
        "## Mailbox Activity",
        f"- Mailbox messages today: {summary.mailbox_messages_today}",
        "",
        "## Conversations",
        *conversation_lines,
        "",
        "## Persona Refinement",
        f"- Last refined at: {summary.persona_last_refined_at or 'not yet refined'}",
        f"- Latest refinement source: {summary.latest_refinement_source or 'unknown'}",
        f"- Significant persona change today: {'yes' if summary.significant_persona_change_today else 'no'}",
        "- Open questions:",
        *open_question_lines,
        "",
        "## Relationship Cache",
        *relationship_lines,
    ]
    return "\n".join(lines) + "\n"
