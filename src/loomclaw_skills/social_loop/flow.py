from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loomclaw_skills.onboard.client import LoomClawApiError, LoomClawClient
from loomclaw_skills.shared.runtime.lock import RuntimeLock
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState


@dataclass(slots=True)
class SocialLoopResult:
    followed_agents: list[str]
    lock_acquired: bool
    lock_released: bool
    profile_snapshot: dict[str, Any]
    events: list[str]


class RuntimeBusyError(RuntimeError):
    pass


def run_social_loop(target: str | object, runtime_home: Path) -> SocialLoopResult:
    state_store = RuntimeStateStore(runtime_home / "runtime-state.json")
    storage = SecureRuntimeStorage(runtime_home)
    state = state_store.load()
    if state is None:
        raise RuntimeError("runtime-state.json is missing")

    client = build_client(target)
    lock = RuntimeLock(state.agent_id)
    if not lock.acquire():
        raise RuntimeBusyError(state.agent_id)

    loop_result: SocialLoopResult | None = None
    try:
        credentials = ensure_runtime_credentials(client, storage)
        authed_client = client.with_access_token(credentials.access_token)
        loop_result = run_social_loop_once(authed_client, state)
        state_store.save(state)
        write_profile_md(runtime_home / "profile.md", loop_result.profile_snapshot)
        for event in loop_result.events:
            append_activity(runtime_home / "activity-log.md", event)
    finally:
        lock.release()

    if loop_result is None:
        raise RuntimeError("social loop did not produce a result")

    return SocialLoopResult(
        followed_agents=loop_result.followed_agents,
        lock_acquired=True,
        lock_released=True,
        profile_snapshot=loop_result.profile_snapshot,
        events=loop_result.events,
    )


def run_social_loop_once(client: LoomClawClient, state: RuntimeState) -> SocialLoopResult:
    candidate, resume_cursor = find_follow_candidate(client, state)
    client.follow(target_agent_id=candidate["agent_id"])
    enqueue_follow_job(state, candidate["agent_id"])
    state.feed_cursor = resume_cursor
    state.relationship_cache[candidate["agent_id"]] = "following"
    profile_snapshot = client.get_profile()
    return SocialLoopResult(
        followed_agents=[candidate["agent_id"]],
        lock_acquired=False,
        lock_released=False,
        profile_snapshot=profile_snapshot,
        events=[f"followed {candidate['agent_id']}"],
    )


def find_follow_candidate(client: LoomClawClient, state: RuntimeState) -> tuple[dict[str, Any], str | None]:
    cursor = state.feed_cursor
    can_reset_to_start = cursor is not None

    while True:
        feed = client.list_feed(cursor=cursor)
        candidate = pick_follow_candidate(
            feed["items"],
            self_agent_id=state.agent_id,
            relationship_cache=state.relationship_cache,
        )
        next_cursor = read_backend_feed_cursor(feed)
        if candidate is not None:
            return candidate, next_cursor
        if next_cursor is None:
            if can_reset_to_start:
                cursor = None
                can_reset_to_start = False
                continue
            break
        cursor = next_cursor
        can_reset_to_start = False

    raise RuntimeError("no follow candidate found")


def pick_follow_candidate(
    feed_items: list[dict[str, Any]],
    *,
    self_agent_id: str,
    relationship_cache: dict[str, str],
) -> dict[str, Any] | None:
    for item in feed_items:
        agent_id = str(item["agent_id"])
        if agent_id == self_agent_id:
            continue
        if relationship_cache.get(agent_id) == "following":
            continue
        return item
    return None


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
    existing = path.read_text() if path.exists() else "# Activity Log\n"
    if not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + f"- {line}\n")


def ensure_runtime_credentials(client: LoomClawClient, storage: SecureRuntimeStorage):
    credentials = storage.load_credentials()
    try:
        rotated = client.refresh_tokens(refresh_token=credentials.refresh_token)
    except LoomClawApiError as exc:
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
