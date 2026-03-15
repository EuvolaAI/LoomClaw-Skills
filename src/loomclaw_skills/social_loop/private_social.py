from __future__ import annotations

from typing import Any

from loomclaw_skills.onboard.client import LoomClawApiError, LoomClawClient
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState
from loomclaw_skills.social_loop.conversation import append_conversation_markdown


LOW_SIGNAL_MARKERS = ("spam", "spammy", "misaligned", "malicious")


def maybe_send_friend_request(client: LoomClawClient, state: RuntimeState, candidate_id: str) -> dict[str, Any]:
    request = client.create_friend_request(target_agent_id=candidate_id)
    state.relationship_cache[candidate_id] = "request_pending"
    state.pending_jobs.append(f"friend_request:{request['request_id']}")
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
    state.pending_jobs.append(f"friend_request_decision:{request_id}:{decision}")


def poll_mailbox(client: LoomClawClient, state: RuntimeState, runtime_home) -> list[dict[str, Any]]:
    try:
        inbox = client.get_mail_inbox()
    except LoomClawApiError as exc:
        if exc.status in {404, 405}:
            return []
        raise
    except AssertionError:
        return []
    items = list(inbox.get("items", []))
    for item in items:
        peer_agent_id = str(item.get("from_agent_id") or item.get("peer_agent_id"))
        append_conversation_markdown(
            runtime_home / "conversations" / f"{peer_agent_id}.md",
            direction="inbound",
            sender=peer_agent_id,
            content=str(item["content_md"]),
            created_at=str(item["created_at"]),
        )
        state.pending_jobs.append(f"reply:{item['message_id']}")
        client.mark_mail_read(message_id=str(item["message_id"]))
    return items
