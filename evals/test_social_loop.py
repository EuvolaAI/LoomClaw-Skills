from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState
from loomclaw_skills.social_loop.flow import run_social_loop


@dataclass
class FakeBackend:
    base_url: str
    session: httpx.Client
    access_tokens: dict[str, dict[str, str]] = field(default_factory=dict)
    profiles: dict[str, dict[str, str]] = field(default_factory=dict)
    feed_items: list[dict[str, str]] = field(default_factory=list)
    followed_pairs: set[tuple[str, str]] = field(default_factory=set)

    def close(self) -> None:
        self.session.close()


@pytest.fixture
def temp_runtime_home(tmp_path: Path) -> Path:
    return tmp_path / "runtime-home"


@pytest.fixture
def fake_backend() -> FakeBackend:
    state = FakeBackend(
        base_url="https://loomclaw.test",
        session=httpx.Client(),
        access_tokens={
            "access": {
                "agent_id": "agent-1",
                "runtime_id": "runtime-1",
            }
        },
        profiles={
            "agent-1": {
                "agent_id": "agent-1",
                "display_name": "Loom Persona",
                "bio": "Agent bio",
                "publication_state": "published",
                "discoverability_state": "discoverable",
            }
        },
        feed_items=[
            {
                "post_id": "post-1",
                "agent_id": "agent-2",
                "type": "intro",
                "content_md": "hello",
                "discoverability_state": "discoverable",
            },
            {
                "post_id": "post-2",
                "agent_id": "agent-3",
                "type": "freeform",
                "content_md": "ignore me",
                "discoverability_state": "indexing_pending",
            },
        ],
    )

    def decode_agent(request: httpx.Request) -> dict[str, str]:
        authorization = request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ").strip()
        if token not in state.access_tokens:
            raise AssertionError(f"unknown access token: {token}")
        return state.access_tokens[token]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/feed":
            return httpx.Response(
                200,
                json={
                    "items": state.feed_items,
                    "next_cursor": "cursor-1",
                },
            )

        if request.method == "GET" and request.url.path == "/v1/profile/me":
            account = decode_agent(request)
            return httpx.Response(200, json=state.profiles[account["agent_id"]])

        if request.method == "POST" and request.url.path == "/v1/follows":
            account = decode_agent(request)
            payload = json.loads(request.content.decode("utf-8") or "{}")
            state.followed_pairs.add((account["agent_id"], payload["target_agent_id"]))
            return httpx.Response(
                201,
                json={
                    "agent_id": account["agent_id"],
                    "target_agent_id": payload["target_agent_id"],
                },
            )

        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    state.session = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=state.base_url,
    )
    yield state
    state.close()


def test_social_loop_fetches_feed_and_writes_activity_log(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(
        RuntimeState(agent_id="agent-1", runtime_id="runtime-1", username="loom"),
    )
    SecureRuntimeStorage(temp_runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="access",
        refresh_token="refresh",
    )

    result = run_social_loop(fake_backend, temp_runtime_home)
    state = RuntimeStateStore(temp_runtime_home / "runtime-state.json").load()

    assert result.followed_agents
    assert state is not None
    assert state.feed_cursor
    assert state.pending_jobs
    assert state.relationship_cache
    assert ("agent-1", "agent-2") in fake_backend.followed_pairs
    assert (temp_runtime_home / "profile.md").exists()
    assert (temp_runtime_home / "activity-log.md").exists()


def test_social_loop_uses_runtime_lock_for_shared_state(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(
        RuntimeState(agent_id="agent-1", runtime_id="runtime-1", username="loom"),
    )
    SecureRuntimeStorage(temp_runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="access",
        refresh_token="refresh",
    )

    result = run_social_loop(fake_backend, temp_runtime_home)

    assert result.lock_acquired is True
    assert result.lock_released is True
