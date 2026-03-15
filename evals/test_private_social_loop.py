from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from loomclaw_skills.shared.persona.state import (
    PersonaPublicProfileDraft,
    PersonaState,
    PersonaStateStore,
)
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState
from loomclaw_skills.social_loop.flow import RuntimeBusyError, run_social_loop
from loomclaw_skills.social_loop.script_runtime import locked_runtime_state


@dataclass
class FakeBackend:
    base_url: str
    session: httpx.Client
    access_tokens: dict[str, dict[str, str]] = field(default_factory=dict)
    refresh_tokens: dict[str, dict[str, str]] = field(default_factory=dict)
    profiles: dict[str, dict[str, str]] = field(default_factory=dict)
    feed_items: list[dict[str, str]] = field(default_factory=list)
    created_friend_requests: list[str] = field(default_factory=list)
    friend_request_inbox: list[dict[str, str]] = field(default_factory=list)
    accepted_request_ids: list[str] = field(default_factory=list)
    rejected_request_ids: list[str] = field(default_factory=list)
    mail_inbox: list[dict[str, str]] = field(default_factory=list)
    read_message_ids: list[str] = field(default_factory=list)

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
        refresh_tokens={
            "refresh": {
                "agent_id": "agent-a",
                "runtime_id": "runtime-a",
            }
        },
        profiles={
            "agent-a": {
                "agent_id": "agent-a",
                "display_name": "Persona A",
                "bio": "Agent bio",
                "publication_state": "published",
                "discoverability_state": "discoverable",
            }
        },
    )

    def decode_agent(request: httpx.Request) -> dict[str, str]:
        authorization = request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ").strip()
        if token not in state.access_tokens:
            raise AssertionError(f"unknown access token: {token}")
        return state.access_tokens[token]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/auth/token/refresh":
            payload = json.loads(request.content.decode("utf-8") or "{}")
            refresh_token = payload["refresh_token"]
            account = state.refresh_tokens[refresh_token]
            next_access_token = "access-rotated"
            next_refresh_token = "refresh-rotated"
            state.access_tokens[next_access_token] = account
            state.refresh_tokens = {next_refresh_token: account}
            return httpx.Response(
                200,
                json={"access_token": next_access_token, "refresh_token": next_refresh_token},
            )

        if request.method == "GET" and request.url.path == "/v1/feed":
            return httpx.Response(200, json={"items": state.feed_items, "next_cursor": None})

        if request.method == "GET" and request.url.path == "/v1/profile/me":
            account = decode_agent(request)
            return httpx.Response(200, json=state.profiles[account["agent_id"]])

        if request.method == "POST" and request.url.path == "/v1/friend-requests":
            payload = json.loads(request.content.decode("utf-8") or "{}")
            state.created_friend_requests.append(payload["target_agent_id"])
            return httpx.Response(
                201,
                json={
                    "request_id": f"request-{len(state.created_friend_requests)}",
                    "from_agent_id": "agent-a",
                    "to_agent_id": payload["target_agent_id"],
                    "status": "pending",
                },
            )

        if request.method == "GET" and request.url.path == "/v1/friend-requests/inbox":
            return httpx.Response(200, json={"items": state.friend_request_inbox})

        if request.method == "POST" and request.url.path.endswith("/accept"):
            request_id = request.url.path.split("/")[-2]
            state.accepted_request_ids.append(request_id)
            return httpx.Response(200, json={"request_id": request_id, "status": "accepted"})

        if request.method == "POST" and request.url.path.endswith("/reject"):
            request_id = request.url.path.split("/")[-2]
            state.rejected_request_ids.append(request_id)
            return httpx.Response(200, json={"request_id": request_id, "status": "rejected"})

        if request.method == "GET" and request.url.path == "/v1/mail/inbox":
            return httpx.Response(200, json={"items": state.mail_inbox})

        if request.method == "POST" and "/v1/mail/messages/" in request.url.path and request.url.path.endswith("/read"):
            message_id = request.url.path.split("/")[-2]
            state.read_message_ids.append(message_id)
            return httpx.Response(204)

        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    state.session = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=state.base_url,
    )
    yield state
    state.close()


def seed_runtime(runtime_home: Path, *, agent_id: str = "agent-a") -> None:
    RuntimeStateStore(runtime_home / "runtime-state.json").save(
        RuntimeState(
            agent_id=agent_id,
            runtime_id=f"runtime-{agent_id}",
            username="loom",
            relationship_cache={},
        )
    )
    SecureRuntimeStorage(runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="stale-access",
        refresh_token="refresh",
    )


def seed_runtime_with_friendship(runtime_home: Path, agent_id: str, peer_agent_id: str) -> None:
    seed_runtime(runtime_home, agent_id=agent_id)
    store = RuntimeStateStore(runtime_home / "runtime-state.json")
    state = store.load()
    assert state is not None
    state.relationship_cache[peer_agent_id] = "friend"
    store.save(state)


def seed_runtime_with_persona(runtime_home: Path, *, agent_id: str = "agent-a") -> None:
    seed_runtime(runtime_home, agent_id=agent_id)
    PersonaStateStore(runtime_home / "persona-memory.json").save(
        PersonaState(
            persona_id="persona-a",
            persona_mode="dedicated_persona_agent",
            active_agent_ref="loomclaw-persona::agent-a",
            public_profile_draft=PersonaPublicProfileDraft(
                display_name="Persona A",
                bio="bio",
            ),
            learning_objectives=["learn"],
            style_profile={"traits": ["curious"]},
            open_questions=["Is the owner more reflective than direct?"],
            local_collaborator_agents=["planner"],
        )
    )


def seed_local_acp_observation(
    runtime_home: Path,
    *,
    source_agent_id: str,
    traits: list[str],
    confidence: float,
) -> None:
    inbox = runtime_home / "acp-observations"
    inbox.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_agent_id": source_agent_id,
        "observed_at": "2026-03-15T11:00:00Z",
        "traits": traits,
        "confidence": confidence,
        "evidence_summary": "structured observation summary",
        "privacy_flags": ["owner-private"],
    }
    (inbox / f"{source_agent_id}.json").write_text(json.dumps(payload))


def load_runtime_state(runtime_home: Path) -> RuntimeState:
    state = RuntimeStateStore(runtime_home / "runtime-state.json").load()
    assert state is not None
    return state


def load_persona_state(runtime_home: Path) -> PersonaState:
    state = PersonaStateStore(runtime_home / "persona-memory.json").load()
    assert state is not None
    return state


def test_social_loop_sends_friend_request_and_updates_relationship_cache(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    seed_runtime(temp_runtime_home, agent_id="agent-a")
    state = load_runtime_state(temp_runtime_home)
    state.relationship_cache["agent-b"] = "following"
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(state)
    fake_backend.feed_items = [
        {"post_id": "post-1", "agent_id": "agent-b", "type": "intro", "content_md": "hello"}
    ]

    result = run_social_loop(fake_backend, temp_runtime_home)
    state = load_runtime_state(temp_runtime_home)

    assert result.sent_friend_requests == ["agent-b"]
    assert state.relationship_cache["agent-b"] == "request_pending"


def test_social_loop_reads_mailbox_and_appends_conversation_md(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    seed_runtime_with_friendship(temp_runtime_home, "agent-a", "agent-b")
    fake_backend.mail_inbox = [
        {
            "message_id": "message-1",
            "from_agent_id": "agent-b",
            "to_agent_id": "agent-a",
            "content_md": "hello",
            "created_at": "2026-03-15T00:00:00Z",
        }
    ]

    result = run_social_loop(fake_backend, temp_runtime_home)

    assert result.received_messages == 1
    assert (temp_runtime_home / "conversations" / "agent-b.md").exists()


def test_social_loop_accepts_and_rejects_friend_requests(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    seed_runtime(temp_runtime_home, agent_id="agent-a")
    fake_backend.friend_request_inbox = [
        {
            "request_id": "request-1",
            "from_agent_id": "agent-b",
            "to_agent_id": "agent-a",
            "summary": "shared goals and thoughtful collaboration",
        },
        {
            "request_id": "request-2",
            "from_agent_id": "agent-c",
            "to_agent_id": "agent-a",
            "summary": "spammy and misaligned",
        },
    ]

    result = run_social_loop(fake_backend, temp_runtime_home)
    state = load_runtime_state(temp_runtime_home)

    assert result.accepted_friend_requests == ["agent-b"]
    assert result.rejected_friend_requests == ["agent-c"]
    assert state.relationship_cache["agent-b"] == "friend"
    assert state.relationship_cache["agent-c"] == "rejected"


def test_social_loop_refines_persona_from_acp_observations(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    seed_runtime_with_persona(temp_runtime_home, agent_id="agent-a")
    seed_local_acp_observation(
        temp_runtime_home,
        source_agent_id="planner",
        traits=["thoughtful"],
        confidence=0.8,
    )

    result = run_social_loop(fake_backend, temp_runtime_home)
    persona = load_persona_state(temp_runtime_home)

    assert result.persona_observations_processed == 1
    assert "thoughtful" in persona.style_profile["traits"]


def test_social_loop_rejects_untrusted_acp_observation_without_merging_traits(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    seed_runtime_with_persona(temp_runtime_home, agent_id="agent-a")
    seed_local_acp_observation(
        temp_runtime_home,
        source_agent_id="stranger",
        traits=["manipulative"],
        confidence=0.9,
    )

    result = run_social_loop(fake_backend, temp_runtime_home)
    persona = load_persona_state(temp_runtime_home)

    assert result.persona_observations_processed == 0
    assert "manipulative" not in persona.style_profile["traits"]
    assert (temp_runtime_home / "acp-observations" / "rejected" / "stranger.json").exists()


def test_locked_runtime_state_blocks_concurrent_script_access(temp_runtime_home: Path) -> None:
    seed_runtime(temp_runtime_home, agent_id="agent-a")

    with locked_runtime_state(temp_runtime_home):
        with pytest.raises(RuntimeBusyError):
            with locked_runtime_state(temp_runtime_home):
                pass
