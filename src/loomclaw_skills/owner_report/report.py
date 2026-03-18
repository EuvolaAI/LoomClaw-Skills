from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from loomclaw_skills.shared.persona.state import PersonaStateStore
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.schemas.report import OwnerReport, ReportResult
from loomclaw_skills.shared.skill_bundle.update_state import BundleUpdateStateStore, resolve_bundle_manager_root


TIMESTAMP_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})T")
ACTIVITY_RE = re.compile(r"^- \[(?P<timestamp>[^\]]+)\] (?P<event>.+)$")


def generate_owner_report(runtime_home: Path, *, today: date | None = None) -> ReportResult:
    report_date = today or date.today()
    state = RuntimeStateStore(runtime_home / "runtime-state.json").load()
    if state is None:
        raise RuntimeError("runtime-state.json is missing")

    persona = PersonaStateStore(runtime_home / "persona-memory.json").load()
    bundle_state = BundleUpdateStateStore(resolve_bundle_manager_root() / "bundle-state.json").load()
    conversation_files = sorted(path.name for path in (runtime_home / "conversations").glob("*.md"))
    bridge_files = sorted(path.name for path in (runtime_home / "bridge").glob("*.md"))
    summary = OwnerReport(
        sent_friend_requests=count_activity(runtime_home, report_date, prefix="sent friend request"),
        accepted_friend_requests=count_activity(runtime_home, report_date, prefix="accepted friend request"),
        pending_friend_requests=len([value for value in state.relationship_cache.values() if value == "request_pending"]),
        mailbox_messages_today=count_mailbox_messages(runtime_home, report_date),
        bridge_recommendations_today=count_activity(runtime_home, report_date, prefix="created bridge recommendation"),
        bridge_invitations_today=count_activity(runtime_home, report_date, prefix="submitted bridge invitation"),
        accepted_bridge_invitations_today=count_activity(
            runtime_home,
            report_date,
            prefix="accepted bridge invitation",
        ),
        pending_bridge_invitations=count_pending_bridge_invitations(state),
        pending_runtime_jobs=count_active_runtime_jobs(state, persona),
        persona_last_refined_at=persona.last_refined_at if persona is not None else None,
        latest_refinement_source=persona.last_refinement_source if persona is not None else None,
        significant_persona_change_today=is_significant_change_today(persona, report_date),
        persona_open_questions=persona.open_questions if persona is not None else [],
        relationship_cache=dict(state.relationship_cache),
        conversation_files=conversation_files,
        bridge_files=bridge_files,
        bundle_current_version=bundle_state.current_version if bundle_state is not None else None,
        bundle_channel=bundle_state.channel if bundle_state is not None else None,
        bundle_last_update_status=bundle_state.last_update_status if bundle_state is not None else None,
        bundle_next_check_after=bundle_state.next_check_after if bundle_state is not None else None,
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


def count_pending_bridge_invitations(state) -> int:
    return sum(
        1
        for job in state.pending_jobs
        if job.startswith("bridge:invitation:") or job.startswith("bridge:incoming:")
    )


def count_active_runtime_jobs(state, persona) -> int:
    persona_questions = len(persona.open_questions) if persona is not None else 0
    pending_friend_requests = len([value for value in state.relationship_cache.values() if value == "request_pending"])
    pending_bridge = count_pending_bridge_invitations(state)
    return pending_friend_requests + pending_bridge + persona_questions


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
    bridge_file_lines = [f"- {name}" for name in summary.bridge_files] or ["- none"]
    narrative_lines = render_narrative_summary(summary)
    next_step_lines = render_next_step_lines(summary)

    lines = [
        f"# LoomClaw Daily Report - {report_date.isoformat()}",
        "",
        "## Narrative Summary",
        *narrative_lines,
        "",
        "## Friend Requests",
        f"- Sent friend requests: {summary.sent_friend_requests}",
        f"- Accepted friend requests: {summary.accepted_friend_requests}",
        f"- Pending friend requests: {summary.pending_friend_requests}",
        "",
        "## Mailbox Activity",
        f"- Mailbox messages today: {summary.mailbox_messages_today}",
        "",
        "## Human Bridge",
        f"- Bridge recommendations today: {summary.bridge_recommendations_today}",
        f"- Bridge invitations today: {summary.bridge_invitations_today}",
        f"- Accepted bridge invitations today: {summary.accepted_bridge_invitations_today}",
        f"- Pending bridge invitations: {summary.pending_bridge_invitations}",
        "- Bridge files:",
        *bridge_file_lines,
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
        "## Skills Bundle",
        f"- Current bundle version: {summary.bundle_current_version or 'unknown'}",
        f"- Update channel: {summary.bundle_channel or 'unknown'}",
        f"- Last update status: {summary.bundle_last_update_status or 'unknown'}",
        f"- Next update check after: {summary.bundle_next_check_after or 'not scheduled'}",
        "",
        "## What I'm Watching Next",
        *next_step_lines,
        "",
        "## Relationship Cache",
        *relationship_lines,
    ]
    return "\n".join(lines) + "\n"


def render_narrative_summary(summary: OwnerReport) -> list[str]:
    sent = summary.sent_friend_requests
    accepted = summary.accepted_friend_requests
    mailbox = summary.mailbox_messages_today
    bridge = summary.bridge_recommendations_today + summary.bridge_invitations_today
    return [
        f"- Today I accepted {accepted} new friend request{'s' if accepted != 1 else ''} and sent {sent} outgoing friend request{'s' if sent != 1 else ''}.",
        f"- I processed {mailbox} mailbox message{'s' if mailbox != 1 else ''} and surfaced {bridge} Human Bridge movement{'s' if bridge != 1 else ''}.",
        f"- I am currently watching {len(summary.relationship_cache)} relationship states and {summary.pending_bridge_invitations + summary.pending_friend_requests} live relationship queues.",
        f"- My current LoomClaw skills bundle is {summary.bundle_current_version or 'unknown'} on the {summary.bundle_channel or 'unknown'} channel.",
    ]


def render_next_step_lines(summary: OwnerReport) -> list[str]:
    lines = [
        f"- I am watching {summary.pending_runtime_jobs} pending runtime jobs across friend requests, bridge decisions, and persona open questions.",
    ]
    if summary.persona_open_questions:
        lines.append(f"- Next persona clarification focus: {summary.persona_open_questions[0]}")
    else:
        lines.append("- No persona clarification is waiting right now.")
    if summary.conversation_files:
        lines.append(f"- Active conversation logs available: {', '.join(summary.conversation_files)}")
    else:
        lines.append("- No active conversation logs yet.")
    return lines
