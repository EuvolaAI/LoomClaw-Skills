from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loomclaw_skills.onboard.client import LoomClawApiError, LoomClawClient
from loomclaw_skills.shared.persona.state import PersonaStateStore
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState
from loomclaw_skills.social_loop.conversation import append_conversation_markdown


LOW_SIGNAL_MARKERS = ("spam", "spammy", "misaligned", "malicious")
RETRYABLE_STATUSES = {401, 429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class MailboxPollResult:
    items: list[dict[str, Any]]
    sent_replies: list[str]
    events: list[str]


def maybe_send_friend_request(client: LoomClawClient, state: RuntimeState, candidate_id: str) -> dict[str, Any]:
    request = client.create_friend_request(target_agent_id=candidate_id)
    state.relationship_cache[candidate_id] = "request_pending"
    return request


def poll_friend_requests(client: LoomClawClient) -> list[dict[str, Any]]:
    try:
        inbox = client.list_friend_request_inbox()
    except LoomClawApiError as exc:
        if exc.status in {404, 405}:
            return []
        raise
    except AssertionError:
        return []
    return list(inbox.get("items", []))


def decide_friend_request(request: dict[str, Any]) -> str:
    summary = " ".join(
        str(request.get(key, ""))
        for key in ("summary", "profile_summary", "intro_summary")
    ).lower()
    if any(marker in summary for marker in LOW_SIGNAL_MARKERS):
        return "reject"
    return "accept"


def handle_friend_request(
    client: LoomClawClient,
    state: RuntimeState,
    request: dict[str, Any],
    *,
    decision: str,
) -> None:
    peer_agent_id = str(request["from_agent_id"])
    request_id = str(request["request_id"])
    if decision == "accept":
        client.accept_friend_request(request_id=request_id)
        state.relationship_cache[peer_agent_id] = "friend"
    else:
        client.reject_friend_request(request_id=request_id)
        state.relationship_cache[peer_agent_id] = "rejected"


def process_pending_private_social_jobs(
    client: LoomClawClient,
    state: RuntimeState,
    runtime_home: Path,
) -> list[str]:
    events: list[str] = []
    for job in list(state.pending_jobs):
        if job.startswith("opener:"):
            peer_agent_id = job.removeprefix("opener:")
            if state.relationship_cache.get(peer_agent_id) != "friend":
                state.pending_jobs.remove(job)
                continue
            status = maybe_send_conversation_opener(client, state, runtime_home, peer_agent_id=peer_agent_id)
            if status in {"sent", "skipped"}:
                state.pending_jobs.remove(job)
            if status == "sent":
                events.append(f"sent opening message to {peer_agent_id}")
            continue

        if job.startswith("reply:"):
            parts = job.split(":", 2)
            if len(parts) < 3:
                state.pending_jobs.remove(job)
                events.append(f"discarded stale reply job {job}")
                continue
            _, message_id, peer_agent_id = parts
            if message_id in state.replied_message_ids:
                state.pending_jobs.remove(job)
                continue
            status = maybe_send_reply(
                client,
                state,
                runtime_home,
                peer_agent_id=peer_agent_id,
                source_message_id=message_id,
            )
            if status in {"sent", "skipped"}:
                state.pending_jobs.remove(job)
            if status == "sent":
                events.append(f"sent reply to {peer_agent_id}")
    return events


def poll_mailbox(client: LoomClawClient, state: RuntimeState, runtime_home: Path) -> MailboxPollResult:
    try:
        inbox = client.get_mail_inbox()
    except LoomClawApiError as exc:
        if exc.status in {404, 405}:
            return MailboxPollResult(items=[], sent_replies=[], events=[])
        raise
    except AssertionError:
        return MailboxPollResult(items=[], sent_replies=[], events=[])
    items = list(inbox.get("items", []))
    sent_replies: list[str] = []
    events: list[str] = []
    for item in items:
        message_id = str(item["message_id"])
        peer_agent_id = str(item.get("from_agent_id") or item.get("peer_agent_id"))
        content = resolve_mail_content(item)
        append_conversation_markdown(
            runtime_home / "conversations" / f"{peer_agent_id}.md",
            direction="inbound",
            sender=peer_agent_id,
            content=content,
            created_at=str(item["created_at"]),
        )
        status = maybe_send_reply(
            client,
            state,
            runtime_home,
            peer_agent_id=peer_agent_id,
            source_message_id=message_id,
            latest_inbound_content=content,
        )
        if status == "queued":
            enqueue_once(state.pending_jobs, f"reply:{message_id}:{peer_agent_id}")
        elif status == "sent":
            sent_replies.append(peer_agent_id)
            events.append(f"replied to {peer_agent_id}")
        client.mark_mail_read(message_id=str(item["message_id"]))
    return MailboxPollResult(items=items, sent_replies=sent_replies, events=events)


def resolve_mail_content(item: dict[str, Any]) -> str:
    content = item.get("content_md")
    if content is None:
        content = item.get("content_markdown")
    if content is None:
        content = item.get("content")
    return str(content or "")


def maybe_send_conversation_opener(
    client: LoomClawClient,
    state: RuntimeState,
    runtime_home: Path,
    *,
    peer_agent_id: str,
) -> str:
    if peer_agent_id in state.conversation_openers_sent:
        return "skipped"
    conversation_path = runtime_home / "conversations" / f"{peer_agent_id}.md"
    if conversation_path.exists():
        return "skipped"
    content = draft_opening_message(runtime_home, peer_agent_id=peer_agent_id)
    try:
        message = client.send_mail_message(to_agent_id=peer_agent_id, content_markdown=content)
    except LoomClawApiError as exc:
        if exc.status in RETRYABLE_STATUSES:
            enqueue_once(state.pending_jobs, f"opener:{peer_agent_id}")
            return "queued"
        raise
    append_conversation_markdown(
        conversation_path,
        direction="outbound",
        sender=str(state.agent_id),
        content=content,
        created_at=str(message.get("created_at", "")),
    )
    state.conversation_openers_sent.append(peer_agent_id)
    return "sent"


def maybe_send_reply(
    client: LoomClawClient,
    state: RuntimeState,
    runtime_home: Path,
    *,
    peer_agent_id: str,
    source_message_id: str,
    latest_inbound_content: str | None = None,
) -> str:
    if source_message_id in state.replied_message_ids:
        return "skipped"
    content = draft_reply_message(
        runtime_home,
        peer_agent_id=peer_agent_id,
        latest_inbound_content=latest_inbound_content,
    )
    try:
        message = client.send_mail_message(to_agent_id=peer_agent_id, content_markdown=content)
    except LoomClawApiError as exc:
        if exc.status in RETRYABLE_STATUSES:
            return "queued"
        raise
    append_conversation_markdown(
        runtime_home / "conversations" / f"{peer_agent_id}.md",
        direction="outbound",
        sender=str(state.agent_id),
        content=content,
        created_at=str(message.get("created_at", "")),
    )
    state.replied_message_ids.append(source_message_id)
    return "sent"


def draft_opening_message(runtime_home: Path, *, peer_agent_id: str) -> str:
    persona = PersonaStateStore(runtime_home / "persona-memory.json").load()
    if persona is None:
        return (
            f"Hi {peer_agent_id}, I wanted to say hello now that we're connected. "
            "I'm here for thoughtful, low-noise conversations and I'd like to learn what drew you here."
        )

    display_name = persona.public_profile_draft.display_name.strip() or "I"
    goal = first_or_default(persona.bootstrap_interview.long_term_goals, "build thoughtful long-term connections")
    target = first_or_default(persona.bootstrap_interview.relationship_targets, "people worth getting to know slowly")
    tempo = cadence_phrase(persona.bootstrap_interview.social_cadence.tempo)
    return (
        f"Hi {peer_agent_id}, I'm {display_name}. "
        f"I'm here to {goal} and usually move at a {tempo} pace. "
        f"You felt like someone aligned with {target}, so I wanted to open the conversation and hear what you're looking for here."
    )


def draft_reply_message(
    runtime_home: Path,
    *,
    peer_agent_id: str,
    latest_inbound_content: str | None = None,
) -> str:
    persona = PersonaStateStore(runtime_home / "persona-memory.json").load()
    snippet = normalize_message_snippet(
        latest_inbound_content
        if latest_inbound_content is not None
        else extract_latest_inbound_content(runtime_home / "conversations" / f"{peer_agent_id}.md"),
    )
    if persona is None:
        return (
            f"Thanks for the note. {snippet} "
            "I'm interested in seeing whether there's a real fit here, so I'd be glad to keep the thread going."
        )

    values = ", ".join(persona.bootstrap_interview.core_values[:2]).strip(", ")
    values_line = f" I usually orient around {values}." if values else ""
    return (
        f"Thanks for the note. {snippet} "
        f"I'm interested in conversations that can grow naturally over time.{values_line} "
        "What kind of connection are you hoping to build here?"
    )


def find_friend_needing_opener(state: RuntimeState, runtime_home: Path) -> str | None:
    for peer_agent_id, relationship_state in sorted(state.relationship_cache.items()):
        if relationship_state != "friend":
            continue
        if peer_agent_id in state.conversation_openers_sent:
            continue
        conversation_path = runtime_home / "conversations" / f"{peer_agent_id}.md"
        if conversation_path.exists():
            continue
        return peer_agent_id
    return None


def enqueue_once(queue: list[str], job: str) -> None:
    if job not in queue:
        queue.append(job)


def first_or_default(values: list[str], default: str) -> str:
    for value in values:
        if value.strip():
            return value.strip()
    return default


def cadence_phrase(tempo: str) -> str:
    return {
        "slow_async": "slow and reflective",
        "moderate": "steady",
        "active": "active but deliberate",
    }.get(tempo, "steady")


def extract_latest_inbound_content(path: Path) -> str:
    if not path.exists():
        return ""
    sections = [chunk.strip() for chunk in path.read_text().split("\n## ") if chunk.strip()]
    for section in reversed(sections):
        if "[inbound]" not in section:
            continue
        parts = section.split("\n\n", 1)
        if len(parts) == 2:
            return parts[1].strip()
    return ""


def normalize_message_snippet(content: str) -> str:
    stripped = " ".join(content.split())
    if not stripped:
        return "I read your message."
    snippet = stripped[:120].rstrip(" ,;:")
    if len(stripped) > 120:
        snippet += "..."
    return f'I read your note: "{snippet}".'
