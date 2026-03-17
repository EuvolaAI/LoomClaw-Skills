from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator

from loomclaw_skills.human_bridge.local_log import append_bridge_inbox_log, append_bridge_log
from loomclaw_skills.onboard.client import LoomClawApiError, LoomClawClient
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState
from loomclaw_skills.social_loop.flow import build_client, ensure_runtime_credentials
from loomclaw_skills.social_loop.flow import append_activity
from loomclaw_skills.social_loop.script_runtime import locked_runtime_state


ConsentSource = Literal[
    "agent_recommendation_only",
    "owner_confirmed_locally",
    "owner_declined_locally",
]
InvitationDecision = Literal["accepted", "rejected"]


class BridgeContext(BaseModel):
    peer_agent_id: str
    summary_markdown: str
    consent_source: ConsentSource = "agent_recommendation_only"
    recommendation_id: str | None = None
    invitation_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_owner_decision(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if "consent_source" not in value and value.get("owner_decision") is not None:
            value = dict(value)
            value["consent_source"] = value["owner_decision"]
        return value


@dataclass(slots=True)
class BridgeResult:
    recommendation_id: str | None
    invitation_id: str | None
    incoming_invitation_ids: list[str]
    lock_acquired: bool
    lock_released: bool


@dataclass(slots=True)
class InvitationResponseResult:
    invitation_id: str
    decision: InvitationDecision
    peer_agent_id: str
    status: str
    lock_acquired: bool
    lock_released: bool


def run_human_bridge(target: str | object, runtime_home: Path) -> BridgeResult:
    client = _build_authed_client(target, runtime_home)

    with locked_runtime_state(runtime_home) as state:
        context = load_or_derive_bridge_context(runtime_home, state=state)
        recommendation_id: str | None = None
        invitation_id: str | None = None
        if context is None:
            reconcile_outgoing_bridge_invitations(client, runtime_home, state)
            incoming_invitation_ids = poll_bridge_invitation_inbox(client, runtime_home, state)
            return BridgeResult(
                recommendation_id=None,
                invitation_id=None,
                incoming_invitation_ids=incoming_invitation_ids,
                lock_acquired=True,
                lock_released=True,
            )

        recommendation_id = context.recommendation_id
        if recommendation_id is None:
            recommendation = create_bridge_recommendation(
                client,
                runtime_home,
                peer_agent_id=context.peer_agent_id,
                summary_markdown=context.summary_markdown,
                consent_source="agent_recommendation_only",
            )
            recommendation_id = str(recommendation["recommendation_id"])
            append_activity(
                runtime_home / "activity-log.md",
                f"created bridge recommendation for {context.peer_agent_id}",
            )
            context.recommendation_id = recommendation_id
            save_bridge_context(runtime_home, context)
        _append_pending_job(state, f"bridge:recommendation:{recommendation_id}")

        invitation_id = context.invitation_id
        if invitation_id is None:
            invitation = maybe_submit_invitation(
                client,
                runtime_home,
                peer_agent_id=context.peer_agent_id,
                summary_markdown=context.summary_markdown,
                consent_source=context.consent_source,
                recommendation_id=recommendation_id,
            )
            invitation_id = None if invitation is None else str(invitation["invitation_id"])
            if invitation_id is not None:
                append_activity(
                    runtime_home / "activity-log.md",
                    f"submitted bridge invitation to {context.peer_agent_id}",
                )
                context.invitation_id = invitation_id
                save_bridge_context(runtime_home, context)
        if invitation_id is not None:
            _append_pending_job(state, f"bridge:invitation:{invitation_id}")

        reconcile_outgoing_bridge_invitations(client, runtime_home, state)
        incoming_invitation_ids = poll_bridge_invitation_inbox(client, runtime_home, state)

    return BridgeResult(
        recommendation_id=recommendation_id,
        invitation_id=invitation_id,
        incoming_invitation_ids=incoming_invitation_ids,
        lock_acquired=True,
        lock_released=True,
    )


def run_bridge_recommendation(target: str | object, runtime_home: Path) -> BridgeResult:
    client = _build_authed_client(target, runtime_home)

    with locked_runtime_state(runtime_home) as state:
        context = load_or_derive_bridge_context(runtime_home, state=state)
        if context is None:
            return BridgeResult(
                recommendation_id=None,
                invitation_id=None,
                incoming_invitation_ids=[],
                lock_acquired=True,
                lock_released=True,
            )
        recommendation = create_bridge_recommendation(
            client,
            runtime_home,
            peer_agent_id=context.peer_agent_id,
            summary_markdown=context.summary_markdown,
            consent_source="agent_recommendation_only",
        )
        recommendation_id = str(recommendation["recommendation_id"])
        _append_pending_job(state, f"bridge:recommendation:{recommendation_id}")
        context.recommendation_id = recommendation_id
        save_bridge_context(runtime_home, context)

    return BridgeResult(
        recommendation_id=recommendation_id,
        invitation_id=None,
        incoming_invitation_ids=[],
        lock_acquired=True,
        lock_released=True,
    )


def sync_bridge_invitation_inbox(target: str | object, runtime_home: Path) -> list[str]:
    client = _build_authed_client(target, runtime_home)

    with locked_runtime_state(runtime_home) as state:
        reconcile_outgoing_bridge_invitations(client, runtime_home, state)
        return poll_bridge_invitation_inbox(client, runtime_home, state)


def respond_to_bridge_invitation(
    target: str | object,
    runtime_home: Path,
    *,
    invitation_id: str,
    decision: InvitationDecision,
    consent_source: ConsentSource,
) -> InvitationResponseResult:
    if decision == "accepted" and consent_source != "owner_confirmed_locally":
        raise RuntimeError("accepted bridge invitations require owner_confirmed_locally")
    if decision == "rejected" and consent_source != "owner_declined_locally":
        raise RuntimeError("rejected bridge invitations require owner_declined_locally")

    client = _build_authed_client(target, runtime_home)
    with locked_runtime_state(runtime_home) as state:
        updated = client.respond_bridge_invitation(
            invitation_id=invitation_id,
            decision=decision,
            consent_source=consent_source,
        )
        peer_agent_id = str(updated["from_agent_id"])
        append_bridge_inbox_log(runtime_home / "bridge" / "inbox.md", updated)
        _remove_pending_job(state, f"bridge:incoming:{invitation_id}")
        append_activity(
            runtime_home / "activity-log.md",
            f"{decision} bridge invitation from {peer_agent_id}",
        )

    return InvitationResponseResult(
        invitation_id=str(updated["invitation_id"]),
        decision=decision,
        peer_agent_id=peer_agent_id,
        status=str(updated["status"]),
        lock_acquired=True,
        lock_released=True,
    )


def create_bridge_recommendation(
    client: LoomClawClient,
    runtime_home: Path,
    *,
    peer_agent_id: str,
    summary_markdown: str,
    consent_source: ConsentSource,
) -> dict[str, str]:
    recommendation = client.create_bridge_recommendation(
        peer_agent_id=peer_agent_id,
        summary_markdown=summary_markdown,
        consent_source=consent_source,
    )
    append_bridge_log(
        runtime_home / "bridge" / "recommendations.md",
        title="Human Bridge Recommendations",
        entry_id=str(recommendation["recommendation_id"]),
        peer_agent_id=peer_agent_id,
        summary_markdown=summary_markdown,
        created_at=str(recommendation["created_at"]),
        consent_source=consent_source,
        status=str(recommendation["status"]),
    )
    return recommendation


def maybe_submit_invitation(
    client: LoomClawClient,
    runtime_home: Path,
    *,
    peer_agent_id: str,
    summary_markdown: str,
    consent_source: ConsentSource,
    recommendation_id: str,
) -> dict[str, str] | None:
    if consent_source != "owner_confirmed_locally":
        return None

    invitation = client.create_human_social_invitation(
        to_agent_id=peer_agent_id,
        recommendation_id=recommendation_id,
        summary_markdown=summary_markdown,
        consent_source=consent_source,
    )
    append_bridge_log(
        runtime_home / "bridge" / "invitations.md",
        title="Human Bridge Invitations",
        entry_id=str(invitation["invitation_id"]),
        peer_agent_id=peer_agent_id,
        summary_markdown=summary_markdown,
        created_at=str(invitation["created_at"]),
        consent_source=consent_source,
        status=str(invitation["status"]),
    )
    return invitation


def poll_bridge_invitation_inbox(
    client: LoomClawClient,
    runtime_home: Path,
    state: RuntimeState,
) -> list[str]:
    try:
        payload = client.list_bridge_invitation_inbox()
    except LoomClawApiError as exc:
        if exc.status in {404, 405}:
            return []
        raise
    except AssertionError:
        return []

    invitation_ids: list[str] = []
    for invitation in payload.get("items", []):
        invitation_id = str(invitation["invitation_id"])
        append_bridge_inbox_log(runtime_home / "bridge" / "inbox.md", invitation)
        if str(invitation["status"]) != "pending":
            _remove_pending_job(state, f"bridge:incoming:{invitation_id}")
            continue
        _append_pending_job(state, f"bridge:incoming:{invitation_id}")
        invitation_ids.append(invitation_id)
    return invitation_ids


def reconcile_outgoing_bridge_invitations(
    client: LoomClawClient,
    runtime_home: Path,
    state: RuntimeState,
) -> None:
    for job in list(state.pending_jobs):
        if not job.startswith("bridge:invitation:"):
            continue
        invitation_id = job.removeprefix("bridge:invitation:")
        try:
            invitation = client.get_bridge_invitation(invitation_id=invitation_id)
        except LoomClawApiError as exc:
            if exc.status in {404, 405}:
                continue
            raise
        status = str(invitation["status"])
        if status == "pending":
            continue
        append_bridge_log(
            runtime_home / "bridge" / "invitations.md",
            title="Human Bridge Invitations",
            entry_id=str(invitation["invitation_id"]),
            peer_agent_id=str(invitation["to_agent_id"]),
            summary_markdown=str(invitation["summary_markdown"]),
            created_at=str(invitation["created_at"]),
            consent_source=str(invitation["consent_source"]),
            status=status,
        )
        append_activity(
            runtime_home / "activity-log.md",
            f"bridge invitation to {invitation['to_agent_id']} {status}",
        )
        _remove_pending_job(state, job)


def load_bridge_context(runtime_home: Path) -> BridgeContext:
    path = runtime_home / "bridge" / "context.json"
    if not path.exists():
        raise RuntimeError("bridge/context.json is missing")
    return BridgeContext.model_validate_json(path.read_text())


def load_or_derive_bridge_context(runtime_home: Path, *, state: RuntimeState) -> BridgeContext | None:
    path = runtime_home / "bridge" / "context.json"
    if path.exists():
        return BridgeContext.model_validate_json(path.read_text())

    derived = derive_bridge_context(runtime_home, state=state)
    if derived is None:
        return None
    save_bridge_context(runtime_home, derived)
    return derived


def derive_bridge_context(runtime_home: Path, *, state: RuntimeState) -> BridgeContext | None:
    candidates = sorted(
        agent_id
        for agent_id, relationship_state in state.relationship_cache.items()
        if relationship_state == "friend"
    )
    for peer_agent_id in candidates:
        conversation_path = runtime_home / "conversations" / f"{peer_agent_id}.md"
        if not conversation_path.exists():
            continue
        conversation = conversation_path.read_text()
        if conversation.count("## ") < 4:
            continue
        if not conversation_is_recent(conversation):
            continue
        return BridgeContext(
            peer_agent_id=peer_agent_id,
            summary_markdown=render_derived_bridge_summary(peer_agent_id, conversation),
        )
    return None


def save_bridge_context(runtime_home: Path, context: BridgeContext) -> None:
    path = runtime_home / "bridge" / "context.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(context.model_dump_json(indent=2))


def render_derived_bridge_summary(peer_agent_id: str, conversation_markdown: str) -> str:
    message_count = conversation_markdown.count("## ")
    return (
        f"Local relationship history suggests `{peer_agent_id}` may merit owner review as a Human Bridge candidate. "
        "The agents have an established friendship and multiple logged conversation turns, but the final decision should still remain local-first.\n\n"
        f"Evidence summary: {message_count} logged conversation turns across an existing friendship."
    )


def conversation_is_recent(conversation_markdown: str) -> bool:
    timestamps: list[datetime] = []
    for line in conversation_markdown.splitlines():
        if not line.startswith("## "):
            continue
        timestamp_token = line.removeprefix("## ").split(" ", 1)[0]
        try:
            parsed = datetime.fromisoformat(timestamp_token.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        timestamps.append(parsed.astimezone(timezone.utc))
    if not timestamps:
        return False
    freshest = max(timestamps)
    return freshest >= datetime.now(timezone.utc) - timedelta(days=30)


def _build_authed_client(target: str | object, runtime_home: Path) -> LoomClawClient:
    state = RuntimeStateStore(runtime_home / "runtime-state.json").load()
    if state is None:
        raise RuntimeError("runtime-state.json is missing")

    storage = SecureRuntimeStorage(runtime_home)
    client = build_client(target)
    credentials = ensure_runtime_credentials(client, storage)
    return client.with_access_token(credentials.access_token)


def _append_pending_job(state: RuntimeState, job: str) -> None:
    if job not in state.pending_jobs:
        state.pending_jobs.append(job)


def _remove_pending_job(state: RuntimeState, job: str) -> None:
    state.pending_jobs = [item for item in state.pending_jobs if item != job]
