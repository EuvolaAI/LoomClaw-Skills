from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from loomclaw_skills.onboard.flow import load_saved_onboard_result, run_onboard
from loomclaw_skills.shared.persona.state import PersonaPublicProfileDraft, PersonaState, PersonaStateStore
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState


@dataclass
class FakeBackend:
    base_url: str
    session: httpx.Client
    accounts: dict[str, dict[str, str]] = field(default_factory=dict)
    runtimes: dict[str, dict[str, str]] = field(default_factory=dict)
    access_tokens: dict[str, dict[str, str]] = field(default_factory=dict)
    refresh_tokens: dict[str, dict[str, str]] = field(default_factory=dict)
    profiles: dict[str, dict[str, str]] = field(default_factory=dict)
    posts: dict[str, dict[str, str]] = field(default_factory=dict)

    def close(self) -> None:
        self.session.close()


@pytest.fixture
def temp_runtime_home(tmp_path: Path) -> Path:
    return tmp_path / "runtime-home"


@pytest.fixture
def fake_backend() -> FakeBackend:
    state = FakeBackend(base_url="https://loomclaw.test", session=httpx.Client())

    def decode_agent(request: httpx.Request) -> dict[str, str]:
        authorization = request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ").strip()
        if token not in state.access_tokens:
            raise AssertionError(f"unknown access token: {token}")
        return state.access_tokens[token]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8") or "{}")

        if request.method == "POST" and request.url.path == "/v1/auth/register":
            username = payload["username"]
            account_index = len(state.accounts) + 1
            agent_id = f"agent-{account_index}"
            runtime_id = f"runtime-{account_index}"
            state.accounts[username] = {
                "username": username,
                "password": payload["password"],
                "agent_id": agent_id,
                "runtime_id": runtime_id,
            }
            state.runtimes[runtime_id] = state.accounts[username]
            return httpx.Response(201, json={"agent_id": agent_id, "runtime_id": runtime_id})

        if request.method == "POST" and request.url.path == "/v1/auth/token":
            account = state.accounts[payload["username"]]
            assert account["password"] == payload["password"]
            access_token = f"access-{account['runtime_id']}"
            refresh_token = f"refresh-{account['runtime_id']}"
            state.access_tokens[access_token] = account
            state.refresh_tokens[refresh_token] = account
            return httpx.Response(200, json={"access_token": access_token, "refresh_token": refresh_token})

        if request.method == "POST" and request.url.path == "/v1/auth/token/refresh":
            account = state.refresh_tokens[payload["refresh_token"]]
            access_token = f"refreshed-access-{account['runtime_id']}"
            refresh_token = f"refreshed-refresh-{account['runtime_id']}"
            state.access_tokens[access_token] = account
            state.refresh_tokens = {refresh_token: account}
            return httpx.Response(200, json={"access_token": access_token, "refresh_token": refresh_token})

        if request.method == "POST" and request.url.path == "/v1/profile":
            account = decode_agent(request)
            profile = {
                "agent_id": account["agent_id"],
                "display_name": payload["display_name"],
                "bio": payload.get("bio"),
                "publication_state": "draft",
                "discoverability_state": "indexing_pending",
            }
            state.profiles[account["agent_id"]] = profile
            return httpx.Response(200, json=profile)

        if request.method == "POST" and request.url.path == "/v1/posts":
            account = decode_agent(request)
            post_id = f"post-{len(state.posts) + 1}"
            post = {
                "post_id": post_id,
                "agent_id": account["agent_id"],
                "type": payload["type"],
                "content_md": payload["content_md"],
            }
            state.posts[post_id] = post
            return httpx.Response(201, json=post)

        if request.method == "POST" and request.url.path == "/v1/profile/onboarding-complete":
            account = decode_agent(request)
            profile = state.profiles[account["agent_id"]]
            profile["publication_state"] = "published"
            profile["discoverability_state"] = "discoverable"
            profile["intro_post_id"] = payload["intro_post_id"]
            return httpx.Response(200, json=profile)

        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    state.session = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=state.base_url,
    )
    yield state
    state.close()


def test_onboard_flow_persists_agent_and_runtime_ids(fake_backend: FakeBackend, temp_runtime_home: Path) -> None:
    result = run_onboard(fake_backend, temp_runtime_home)

    assert result.agent_id
    assert result.runtime_id
    assert result.persona_id
    assert result.persona_mode in {"dedicated_persona_agent", "bound_existing_agent"}
    assert result.profile["display_name"]

    state = RuntimeStateStore(temp_runtime_home / "runtime-state.json").load()
    assert state is not None
    assert state.agent_id == result.agent_id
    assert state.runtime_id == result.runtime_id
    assert state.persona_id == result.persona_id
    assert state.persona_mode == result.persona_mode

    creds = SecureRuntimeStorage(temp_runtime_home).load_credentials()
    assert creds.username
    assert creds.password
    assert creds.access_token
    assert creds.refresh_token
    assert (temp_runtime_home / "persona-memory.json").exists()


def test_onboard_publishes_intro_post(fake_backend: FakeBackend, temp_runtime_home: Path) -> None:
    result = run_onboard(fake_backend, temp_runtime_home)

    assert result.intro_post_id
    assert result.intro_post_id in fake_backend.posts


def test_onboard_finishes_publication_state(fake_backend: FakeBackend, temp_runtime_home: Path) -> None:
    result = run_onboard(fake_backend, temp_runtime_home)

    assert result.publication_state == "published"
    assert result.discoverability_state == "discoverable"


def test_onboard_falls_back_to_bound_existing_agent_when_persona_creation_fails(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    result = run_onboard(fake_backend, temp_runtime_home, force_bind_existing=True)

    assert result.persona_mode == "bound_existing_agent"


def test_onboard_is_restart_safe_and_reuses_saved_state(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOOMCLAW_PERSONA_DISPLAY_NAME", "First Persona")
    first = run_onboard(fake_backend, temp_runtime_home)

    monkeypatch.setenv("LOOMCLAW_PERSONA_DISPLAY_NAME", "Changed Persona")
    second = run_onboard(fake_backend, temp_runtime_home)

    assert second.agent_id == first.agent_id
    assert second.runtime_id == first.runtime_id
    assert second.persona_id == first.persona_id
    assert second.profile["display_name"] == "First Persona"
    assert len(fake_backend.accounts) == 1
    assert len(fake_backend.posts) == 1


def test_load_saved_onboard_result_uses_persisted_persona_draft(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOOMCLAW_PERSONA_DISPLAY_NAME", "Stored Persona")
    run_onboard(fake_backend, temp_runtime_home)

    monkeypatch.setenv("LOOMCLAW_PERSONA_DISPLAY_NAME", "Changed Persona")
    saved = load_saved_onboard_result(temp_runtime_home)

    assert saved is not None
    assert saved.profile["display_name"] == "Stored Persona"


def test_onboard_resume_refreshes_saved_tokens_before_finishing_partial_state(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    fake_backend.accounts["loom"] = {
        "username": "loom",
        "password": "pw",
        "agent_id": "agent-1",
        "runtime_id": "runtime-1",
    }
    fake_backend.runtimes["runtime-1"] = fake_backend.accounts["loom"]
    fake_backend.refresh_tokens["refresh-runtime-1"] = fake_backend.accounts["loom"]
    fake_backend.profiles["agent-1"] = {
        "agent_id": "agent-1",
        "display_name": "Stored Persona",
        "bio": "Stored bio",
        "publication_state": "draft",
        "discoverability_state": "indexing_pending",
    }
    PersonaStateStore(temp_runtime_home / "persona-memory.json").save(
        PersonaState(
            persona_id="persona-1",
            persona_mode="dedicated_persona_agent",
            active_agent_ref="loomclaw-persona::resume",
            public_profile_draft=PersonaPublicProfileDraft(
                display_name="Stored Persona",
                bio="Stored bio",
            ),
            learning_objectives=[],
        )
    )
    RuntimeStateStore(temp_runtime_home / "runtime-state.json").save(
        RuntimeState(
            agent_id="agent-1",
            runtime_id="runtime-1",
            username="loom",
            persona_id="persona-1",
            persona_mode="dedicated_persona_agent",
            intro_post_id=None,
            publication_state="draft",
            discoverability_state="indexing_pending",
        )
    )
    SecureRuntimeStorage(temp_runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="stale-access-runtime-1",
        refresh_token="refresh-runtime-1",
    )

    result = run_onboard(fake_backend, temp_runtime_home)
    credentials = SecureRuntimeStorage(temp_runtime_home).load_credentials()

    assert result.publication_state == "published"
    assert result.intro_post_id is not None
    assert credentials.access_token == "refreshed-access-runtime-1"
    assert credentials.refresh_token == "refreshed-refresh-runtime-1"
