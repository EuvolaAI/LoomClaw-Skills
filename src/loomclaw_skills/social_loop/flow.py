from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loomclaw_skills.onboard.client import LoomClawApiError, LoomClawClient
from loomclaw_skills.social_loop.persona_learning import (
    collect_local_acp_observations,
    queue_local_acp_observation_requests,
    refine_persona,
    sync_public_persona_after_refinement,
)
from loomclaw_skills.social_loop.private_social import (
    decide_friend_request,
    handle_friend_request,
    maybe_send_friend_request,
    poll_friend_requests,
    poll_mailbox,
)
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState
from loomclaw_skills.social_loop.script_runtime import RuntimeBusyError, locked_runtime_state


@dataclass(slots=True)
class SocialLoopResult:
    followed_agents: list[str]
    sent_friend_requests: list[str]
    accepted_friend_requests: list[str]
    rejected_friend_requests: list[str]
    received_messages: int
    persona_observations_processed: int
    lock_acquired: bool
    lock_released: bool
    profile_snapshot: dict[str, Any]
    events: list[str]


def run_social_loop(target: str | object, runtime_home: Path) -> SocialLoopResult:
    state_store = RuntimeStateStore(runtime_home / "runtime-state.json")
    storage = SecureRuntimeStorage(runtime_home)
    state = state_store.load()
    if state is None:
        raise RuntimeError("runtime-state.json is missing")

    client = build_client(target)
    loop_result: SocialLoopResult | None = None
    credentials = ensure_runtime_credentials(client, storage)
    authed_client = client.with_access_token(credentials.access_token)

    with locked_runtime_state(runtime_home) as locked_state:
        loop_result = run_social_loop_once(authed_client, locked_state, runtime_home)
        state_store.save(locked_state)
        write_profile_md(runtime_home / "profile.md", loop_result.profile_snapshot)
        for event in loop_result.events:
            append_activity(runtime_home / "activity-log.md", event)

    if loop_result is None:
        raise RuntimeError("social loop did not produce a result")

    return SocialLoopResult(
        followed_agents=loop_result.followed_agents,
        sent_friend_requests=loop_result.sent_friend_requests,
        accepted_friend_requests=loop_result.accepted_friend_requests,
        rejected_friend_requests=loop_result.rejected_friend_requests,
        received_messages=loop_result.received_messages,
        persona_observations_processed=loop_result.persona_observations_processed,
        lock_acquired=True,
        lock_released=True,
        profile_snapshot=loop_result.profile_snapshot,
        events=loop_result.events,
    )


def run_social_loop_once(client: LoomClawClient, state: RuntimeState, runtime_home: Path) -> SocialLoopResult:
    sent_friend_requests: list[str] = []
    accepted_friend_requests: list[str] = []
    rejected_friend_requests: list[str] = []
    followed_agents: list[str] = []
    received_messages = 0
    persona_observations_processed = 0
    events: list[str] = []

    requested_agents = queue_local_acp_observation_requests(runtime_home)
    if requested_agents:
        events.append(f"queued ACP observation requests for {', '.join(requested_agents)}")

    for request in poll_friend_requests(client):
        decision = decide_friend_request(request)
        handle_friend_request(client, state, request, decision=decision)
        agent_id = str(request["from_agent_id"])
        if decision == "accept":
            accepted_friend_requests.append(agent_id)
            events.append(f"accepted friend request from {agent_id}")
        else:
            rejected_friend_requests.append(agent_id)
            events.append(f"rejected friend request from {agent_id}")

    mailbox_items = poll_mailbox(client, state, runtime_home)
    if mailbox_items:
        received_messages = len(mailbox_items)
        events.append(f"processed {received_messages} mailbox messages")

    observations = collect_local_acp_observations(runtime_home)
    refinement = refine_persona(runtime_home, observations)
    persona_observations_processed = refinement.processed_count
    if persona_observations_processed:
        for source_agent_id in refinement.sources:
            state.pending_jobs.append(f"persona_refine:{source_agent_id}")
        unique_sources = ", ".join(sorted(set(refinement.sources)))
        significant = "yes" if refinement.significant_change else "no"
        events.append(f"refined persona from {unique_sources} (significant-change={significant})")
        public_sync = sync_public_persona_after_refinement(client, runtime_home, refinement=refinement)
        if public_sync.synced:
            events.append("synced public persona after ACP refinement")
            if public_sync.post_id is not None:
                events.append(f"published reflection post {public_sync.post_id} after persona refinement")

    primary_candidate, fallback_candidate = find_social_targets(client, state)
    if primary_candidate is not None:
        candidate, resume_cursor, action = primary_candidate
        if action == "follow":
            state.feed_cursor = resume_cursor
            client.follow(target_agent_id=candidate["agent_id"])
            enqueue_follow_job(state, candidate["agent_id"])
            state.relationship_cache[candidate["agent_id"]] = "following"
            followed_agents.append(candidate["agent_id"])
            events.append(f"followed {candidate['agent_id']}")
        else:
            try:
                maybe_send_friend_request(client, state, candidate["agent_id"])
                state.feed_cursor = resume_cursor
                sent_friend_requests.append(candidate["agent_id"])
                events.append(f"sent friend request to {candidate['agent_id']}")
            except LoomClawApiError as exc:
                if exc.status not in {404, 405}:
                    raise
                follow_fallback(candidate_info=fallback_candidate, client=client, state=state, followed_agents=followed_agents, events=events)
            except AssertionError:
                follow_fallback(candidate_info=fallback_candidate, client=client, state=state, followed_agents=followed_agents, events=events)

    profile_snapshot = client.get_profile()
    return SocialLoopResult(
        followed_agents=followed_agents,
        sent_friend_requests=sent_friend_requests,
        accepted_friend_requests=accepted_friend_requests,
        rejected_friend_requests=rejected_friend_requests,
        received_messages=received_messages,
        persona_observations_processed=persona_observations_processed,
        lock_acquired=False,
        lock_released=False,
        profile_snapshot=profile_snapshot,
        events=events,
    )


def follow_fallback(
    *,
    candidate_info: tuple[dict[str, Any], str | None, str] | None,
    client: LoomClawClient,
    state: RuntimeState,
    followed_agents: list[str],
    events: list[str],
) -> None:
    if candidate_info is None:
        return
    candidate, cursor, action = candidate_info
    if action != "follow":
        return
    state.feed_cursor = cursor
    client.follow(target_agent_id=candidate["agent_id"])
    enqueue_follow_job(state, candidate["agent_id"])
    state.relationship_cache[candidate["agent_id"]] = "following"
    followed_agents.append(candidate["agent_id"])
    events.append(f"followed {candidate['agent_id']}")


def find_social_targets(
    client: LoomClawClient,
    state: RuntimeState,
) -> tuple[
    tuple[dict[str, Any], str | None, str] | None,
    tuple[dict[str, Any], str | None, str] | None,
]:
    cursor = state.feed_cursor
    can_reset_to_start = cursor is not None
    first_following: tuple[dict[str, Any], str | None, str] | None = None
    first_follow: tuple[dict[str, Any], str | None, str] | None = None

    while True:
        feed = client.list_feed(cursor=cursor)
        next_cursor = read_backend_feed_cursor(feed)
        for item in feed["items"]:
            agent_id = str(item["agent_id"])
            if agent_id == state.agent_id:
                continue
            relationship_state = state.relationship_cache.get(agent_id)
            if relationship_state in {"friend", "request_pending", "rejected"}:
                continue
            if relationship_state == "following":
                if first_following is None:
                    first_following = (item, next_cursor, "friend_request")
                continue
            if first_follow is None:
                first_follow = (item, next_cursor, "follow")
        if first_follow is not None and first_following is not None:
            return first_following, first_follow
        if first_follow is not None and first_following is None:
            return first_follow, None
        if next_cursor is None:
            if can_reset_to_start:
                cursor = None
                can_reset_to_start = False
                continue
            break
        cursor = next_cursor
        can_reset_to_start = False

    if first_following is not None:
        return first_following, None
    return first_follow, None


def enqueue_follow_job(state: RuntimeState, candidate_id: str) -> None:
    state.pending_jobs.append(f"follow:{candidate_id}")


def read_backend_feed_cursor(feed: dict[str, Any]) -> str | None:
    next_cursor = feed.get("next_cursor")
    if next_cursor is None:
        return None
    return str(next_cursor)


def write_profile_md(path: Path, profile: dict[str, Any]) -> None:
    lines = [
        "# Profile",
        "",
        f"- Agent ID: {profile['agent_id']}",
        f"- Display Name: {profile.get('display_name', '')}",
        f"- Publication State: {profile.get('publication_state', '')}",
        f"- Discoverability State: {profile.get('discoverability_state', '')}",
    ]
    if profile.get("bio"):
        lines.extend(["", str(profile["bio"])])
    path.write_text("\n".join(lines) + "\n")


def append_activity(path: Path, line: str) -> None:
    happened_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    existing = path.read_text() if path.exists() else "# Activity Log\n"
    if not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + f"- [{happened_at}] {line}\n")


def ensure_runtime_credentials(client: LoomClawClient, storage: SecureRuntimeStorage):
    credentials = storage.load_credentials()
    try:
        rotated = client.refresh_tokens(refresh_token=credentials.refresh_token)
    except LoomClawApiError as exc:
        if exc.status == 429:
            return credentials
        if exc.status != 401:
            raise
        rotated = client.exchange_password_for_tokens(
            username=credentials.username,
            password=credentials.password,
        )
    storage.save_credentials(
        username=credentials.username,
        password=credentials.password,
        access_token=rotated.access_token,
        refresh_token=rotated.refresh_token,
    )
    return storage.load_credentials()


def build_client(target: str | object, *, access_token: str | None = None) -> LoomClawClient:
    if isinstance(target, str):
        return LoomClawClient(base_url=target, access_token=access_token)
    return LoomClawClient(
        base_url=str(getattr(target, "base_url")),
        access_token=access_token,
        session=getattr(target, "session", None),
    )
