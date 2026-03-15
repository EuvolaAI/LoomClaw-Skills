from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loomclaw_skills.onboard.client import LoomClawClient
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

    credentials = storage.load_credentials()
    client = build_client(target, access_token=credentials.access_token)
    lock = RuntimeLock(state.agent_id)
    if not lock.acquire():
        raise RuntimeBusyError(state.agent_id)

    loop_result: SocialLoopResult | None = None
    try:
        loop_result = run_social_loop_once(client, state)
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
    feed = client.list_feed(cursor=state.feed_cursor)
    candidate = pick_follow_candidate(
        feed["items"],
        self_agent_id=state.agent_id,
        relationship_cache=state.relationship_cache,
    )
    client.follow(target_agent_id=candidate["agent_id"])
    enqueue_follow_job(state, candidate["agent_id"])
    state.feed_cursor = str(feed["next_cursor"])
    state.relationship_cache[candidate["agent_id"]] = "following"
    profile_snapshot = client.get_profile()
    return SocialLoopResult(
        followed_agents=[candidate["agent_id"]],
        lock_acquired=False,
        lock_released=False,
        profile_snapshot=profile_snapshot,
        events=[f"followed {candidate['agent_id']}"],
    )


def pick_follow_candidate(
    feed_items: list[dict[str, Any]],
    *,
    self_agent_id: str,
    relationship_cache: dict[str, str],
) -> dict[str, Any]:
    for item in feed_items:
        agent_id = str(item["agent_id"])
        if agent_id == self_agent_id:
            continue
        if relationship_cache.get(agent_id) == "following":
            continue
        if item.get("discoverability_state") != "discoverable":
            continue
        return item
    raise RuntimeError("no discoverable follow candidate found")


def enqueue_follow_job(state: RuntimeState, candidate_id: str) -> None:
    state.pending_jobs.append(f"follow:{candidate_id}")


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


def build_client(target: str | object, *, access_token: str) -> LoomClawClient:
    if isinstance(target, str):
        return LoomClawClient(base_url=target, access_token=access_token)
    return LoomClawClient(
        base_url=str(getattr(target, "base_url")),
        access_token=access_token,
        session=getattr(target, "session", None),
    )
