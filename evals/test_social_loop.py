from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from loomclaw_skills.onboard.client import LoomClawApiError
from loomclaw_skills.shared.persona.state import (
    PersonaBootstrapInterview,
    PersonaInteractionStyle,
    PersonaPublicProfileDraft,
    PersonaSocialCadence,
    PersonaState,
    PersonaStateStore,
)
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState
from loomclaw_skills.social_loop.flow import run_social_loop


@dataclass
class FakeBackend:
    base_url: str
    session: httpx.Client
    access_tokens: dict[str, dict[str, str]] = field(default_factory=dict)
    refresh_tokens: dict[str, dict[str, str]] = field(default_factory=dict)
    profiles: dict[str, dict[str, str]] = field(default_factory=dict)
    feed_items: list[dict[str, str]] = field(default_factory=list)
    followed_pairs: set[tuple[str, str]] = field(default_factory=set)
    seen_feed_cursors: list[str | None] = field(default_factory=list)
    created_posts: list[dict[str, str]] = field(default_factory=list)
    profile_updates: list[dict[str, str | None]] = field(default_factory=list)

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
        access_tokens={},
        refresh_tokens={
            "refresh": {
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
            },
            {
                "post_id": "post-2",
                "agent_id": "agent-3",
                "type": "freeform",
                "content_md": "ignore me",
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
            cursor = request.url.params.get("cursor")
            state.seen_feed_cursors.append(cursor)
            offset = int(cursor) if cursor else 0
            page = state.feed_items[offset : offset + 20]
            next_cursor = str(offset + 20) if offset + 20 < len(state.feed_items) else None
            return httpx.Response(200, json={"items": page, "next_cursor": next_cursor})

        if request.method == "GET" and request.url.path == "/v1/profile/me":
            account = decode_agent(request)
            return httpx.Response(200, json=state.profiles[account["agent_id"]])

        if request.method == "POST" and request.url.path == "/v1/profile":
            account = decode_agent(request)
            payload = json.loads(request.content.decode("utf-8") or "{}")
            profile = state.profiles[account["agent_id"]]
            profile["display_name"] = payload["display_name"]
            profile["bio"] = payload.get("bio")
            state.profile_updates.append(
                {
                    "display_name": payload["display_name"],
                    "bio": payload.get("bio"),
                }
            )
            return httpx.Response(200, json=profile)

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

        if request.method == "POST" and request.url.path == "/v1/posts":
            account = decode_agent(request)
            payload = json.loads(request.content.decode("utf-8") or "{}")
            post = {
                "post_id": f"post-created-{len(state.created_posts) + 1}",
                "agent_id": account["agent_id"],
                "type": payload["type"],
                "content_md": payload["content_md"],
            }
            state.created_posts.append(post)
            return httpx.Response(201, json=post)

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
        access_token="stale-access",
        refresh_token="refresh",
    )

    result = run_social_loop(fake_backend, temp_runtime_home)
    state = RuntimeStateStore(temp_runtime_home / "runtime-state.json").load()
    credentials = SecureRuntimeStorage(temp_runtime_home).load_credentials()

    assert result.followed_agents
    assert state is not None
    assert state.feed_cursor is None
    assert state.pending_jobs
    assert state.relationship_cache
    assert credentials.access_token == "access-rotated"
    assert credentials.refresh_token == "refresh-rotated"
    assert fake_backend.seen_feed_cursors == [None]
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
        access_token="stale-access",
        refresh_token="refresh",
    )

    result = run_social_loop(fake_backend, temp_runtime_home)

    assert result.lock_acquired is True
    assert result.lock_released is True


def test_social_loop_pages_feed_until_it_finds_an_unfollowed_candidate(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    fake_backend.feed_items = [
        {
            "post_id": f"post-{index}",
            "agent_id": f"agent-{index + 2}",
            "type": "intro",
            "content_md": f"intro-{index}",
        }
        for index in range(21)
    ]
    followed_ids = {f"agent-{index + 2}": "following" for index in range(20)}
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(
        RuntimeState(
            agent_id="agent-1",
            runtime_id="runtime-1",
            username="loom",
            relationship_cache=followed_ids,
        ),
    )
    SecureRuntimeStorage(temp_runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="stale-access",
        refresh_token="refresh",
    )

    result = run_social_loop(fake_backend, temp_runtime_home)

    assert result.followed_agents == ["agent-22"]
    assert ("agent-1", "agent-22") in fake_backend.followed_pairs
    assert fake_backend.seen_feed_cursors == [None, "20"]


def test_social_loop_uses_existing_access_token_when_refresh_is_rate_limited(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def rate_limited_refresh(self, *, refresh_token: str):  # type: ignore[no-untyped-def]
        raise LoomClawApiError(429, "rate limited")

    monkeypatch.setattr(
        "loomclaw_skills.onboard.client.LoomClawClient.refresh_tokens",
        rate_limited_refresh,
    )

    fake_backend.access_tokens["still-valid-access"] = {
        "agent_id": "agent-1",
        "runtime_id": "runtime-1",
    }
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(
        RuntimeState(agent_id="agent-1", runtime_id="runtime-1", username="loom"),
    )
    SecureRuntimeStorage(temp_runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="still-valid-access",
        refresh_token="refresh",
    )

    result = run_social_loop(fake_backend, temp_runtime_home)
    credentials = SecureRuntimeStorage(temp_runtime_home).load_credentials()

    assert result.followed_agents
    assert credentials.access_token == "still-valid-access"
    assert credentials.refresh_token == "refresh"


def test_social_loop_resumes_from_saved_feed_cursor(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    fake_backend.feed_items = [
        {
            "post_id": f"post-{index}",
            "agent_id": f"agent-{index + 2}",
            "type": "intro",
            "content_md": f"intro-{index}",
        }
        for index in range(21)
    ]
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(
        RuntimeState(
            agent_id="agent-1",
            runtime_id="runtime-1",
            username="loom",
            feed_cursor="20",
        ),
    )
    SecureRuntimeStorage(temp_runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="stale-access",
        refresh_token="refresh",
    )

    run_social_loop(fake_backend, temp_runtime_home)

    assert fake_backend.seen_feed_cursors[0] == "20"


def test_social_loop_emits_acp_observation_requests_for_collaborator_agents(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(
        RuntimeState(agent_id="agent-1", runtime_id="runtime-1", username="loom"),
    )
    SecureRuntimeStorage(temp_runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="stale-access",
        refresh_token="refresh",
    )
    PersonaStateStore(temp_runtime_home / "persona-memory.json").save(
        PersonaState(
            persona_id="persona-1",
            persona_mode="dedicated_persona_agent",
            active_agent_ref="loomclaw-persona::agent-1",
            public_profile_draft=PersonaPublicProfileDraft(display_name="Loom Persona", bio="Agent bio"),
            bootstrap_interview=PersonaBootstrapInterview(),
            learning_objectives=[],
            local_collaborator_agents=["planner", "researcher"],
        )
    )

    run_social_loop(fake_backend, temp_runtime_home)

    planner_request = temp_runtime_home / "acp-requests" / "outbox" / "planner.json"
    researcher_request = temp_runtime_home / "acp-requests" / "outbox" / "researcher.json"
    activity_log = (temp_runtime_home / "activity-log.md").read_text()

    assert planner_request.exists()
    assert researcher_request.exists()
    assert json.loads(planner_request.read_text())["target_agent_id"] == "planner"
    assert "queued ACP observation requests for planner, researcher" in activity_log


def test_social_loop_syncs_public_persona_after_significant_refinement(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(
        RuntimeState(agent_id="agent-1", runtime_id="runtime-1", username="loom"),
    )
    SecureRuntimeStorage(temp_runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="stale-access",
        refresh_token="refresh",
    )
    PersonaStateStore(temp_runtime_home / "persona-memory.json").save(
        PersonaState(
            persona_id="persona-1",
            persona_mode="dedicated_persona_agent",
            active_agent_ref="loomclaw-persona::agent-1",
            public_profile_draft=PersonaPublicProfileDraft(
                display_name="Loom Persona",
                bio="A LoomClaw social persona shaped around a stable local persona layer.",
            ),
            bootstrap_interview=PersonaBootstrapInterview(
                interaction_style=PersonaInteractionStyle(
                    directness="gentle",
                    pace="exploratory",
                    expressiveness="reserved",
                ),
                social_cadence=PersonaSocialCadence(
                    connection_depth="few_deep_connections",
                    tempo="slow_async",
                ),
            ),
            learning_objectives=[],
            local_collaborator_agents=["planner"],
        )
    )
    inbox = temp_runtime_home / "acp-observations"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "planner.json").write_text(
        json.dumps(
            {
                "source_agent_id": "planner",
                "observed_at": "2026-03-17T09:00:00Z",
                "confidence": 0.84,
                "traits": ["warm", "deliberate"],
                "evidence_summary": "The owner consistently balances warmth with clear structure.",
                "privacy_flags": [],
            }
        )
    )

    result = run_social_loop(fake_backend, temp_runtime_home)
    activity_log = (temp_runtime_home / "activity-log.md").read_text()

    assert result.persona_observations_processed == 1
    assert fake_backend.profile_updates
    assert "warm" in str(fake_backend.profile_updates[-1]["bio"])
    assert fake_backend.created_posts
    assert fake_backend.created_posts[-1]["type"] == "reflection"
    assert "warm" in fake_backend.created_posts[-1]["content_md"]
    assert "synced public persona after ACP refinement" in activity_log


def test_social_loop_keeps_private_observation_traits_out_of_public_sync(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(
        RuntimeState(agent_id="agent-1", runtime_id="runtime-1", username="loom"),
    )
    SecureRuntimeStorage(temp_runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="stale-access",
        refresh_token="refresh",
    )
    PersonaStateStore(temp_runtime_home / "persona-memory.json").save(
        PersonaState(
            persona_id="persona-1",
            persona_mode="dedicated_persona_agent",
            active_agent_ref="loomclaw-persona::agent-1",
            public_profile_draft=PersonaPublicProfileDraft(
                display_name="Loom Persona",
                bio="A LoomClaw social persona shaped around a stable local persona layer.",
            ),
            bootstrap_interview=PersonaBootstrapInterview(
                private_boundaries=["never reveal medical details"],
            ),
            learning_objectives=[],
            local_collaborator_agents=["planner"],
        )
    )
    inbox = temp_runtime_home / "acp-observations"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "planner.json").write_text(
        json.dumps(
            {
                "source_agent_id": "planner",
                "observed_at": "2026-03-17T09:00:00Z",
                "confidence": 0.91,
                "traits": ["medical-anxious"],
                "evidence_summary": "The owner has private medical worries that shape pacing.",
                "privacy_flags": ["owner-private"],
            }
        )
    )

    run_social_loop(fake_backend, temp_runtime_home)
    persona = PersonaStateStore(temp_runtime_home / "persona-memory.json").load()

    assert persona is not None
    assert "medical-anxious" in persona.style_profile["traits"]
    assert fake_backend.profile_updates == []
    assert fake_backend.created_posts == []


def test_social_loop_respects_owner_private_boundaries_even_without_privacy_flags(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(
        RuntimeState(agent_id="agent-1", runtime_id="runtime-1", username="loom"),
    )
    SecureRuntimeStorage(temp_runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="stale-access",
        refresh_token="refresh",
    )
    PersonaStateStore(temp_runtime_home / "persona-memory.json").save(
        PersonaState(
            persona_id="persona-1",
            persona_mode="dedicated_persona_agent",
            active_agent_ref="loomclaw-persona::agent-1",
            public_profile_draft=PersonaPublicProfileDraft(
                display_name="Loom Persona",
                bio="A LoomClaw social persona shaped around a stable local persona layer.",
            ),
            bootstrap_interview=PersonaBootstrapInterview(
                private_boundaries=["medical", "health"],
            ),
            learning_objectives=[],
            local_collaborator_agents=["planner"],
        )
    )
    inbox = temp_runtime_home / "acp-observations"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "planner.json").write_text(
        json.dumps(
            {
                "source_agent_id": "planner",
                "observed_at": "2026-03-17T09:00:00Z",
                "confidence": 0.92,
                "traits": ["medical-anxious"],
                "evidence_summary": "Health concerns are affecting pacing right now.",
                "privacy_flags": [],
            }
        )
    )

    run_social_loop(fake_backend, temp_runtime_home)
    persona = PersonaStateStore(temp_runtime_home / "persona-memory.json").load()

    assert persona is not None
    assert "medical-anxious" in persona.style_profile["traits"]
    assert fake_backend.profile_updates == []
    assert fake_backend.created_posts == []


def test_social_loop_tracks_private_refinement_as_locally_significant_even_without_public_sync(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(
        RuntimeState(agent_id="agent-1", runtime_id="runtime-1", username="loom"),
    )
    SecureRuntimeStorage(temp_runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="stale-access",
        refresh_token="refresh",
    )
    PersonaStateStore(temp_runtime_home / "persona-memory.json").save(
        PersonaState(
            persona_id="persona-1",
            persona_mode="dedicated_persona_agent",
            active_agent_ref="loomclaw-persona::agent-1",
            public_profile_draft=PersonaPublicProfileDraft(
                display_name="Loom Persona",
                bio="A LoomClaw social persona shaped around a stable local persona layer.",
            ),
            bootstrap_interview=PersonaBootstrapInterview(
                private_boundaries=["medical"],
            ),
            learning_objectives=[],
            local_collaborator_agents=["planner"],
        )
    )
    inbox = temp_runtime_home / "acp-observations"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "planner.json").write_text(
        json.dumps(
            {
                "source_agent_id": "planner",
                "observed_at": "2026-03-17T09:00:00Z",
                "confidence": 0.91,
                "traits": ["medical-anxious"],
                "evidence_summary": "Health concerns are affecting pacing right now.",
                "privacy_flags": [],
            }
        )
    )

    run_social_loop(fake_backend, temp_runtime_home)
    persona = PersonaStateStore(temp_runtime_home / "persona-memory.json").load()
    activity_log = (temp_runtime_home / "activity-log.md").read_text()

    assert persona is not None
    assert persona.last_significant_change_at is not None
    assert "significant-change=yes" in activity_log
