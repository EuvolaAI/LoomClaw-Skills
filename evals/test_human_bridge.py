from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from loomclaw_skills.human_bridge.flow import (
    respond_to_bridge_invitation,
    run_human_bridge,
    sync_bridge_invitation_inbox,
)
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState


@dataclass
class FakeBackend:
    base_url: str
    session: httpx.Client
    access_tokens: dict[str, dict[str, str]] = field(default_factory=dict)
    refresh_tokens: dict[str, dict[str, str]] = field(default_factory=dict)
    recommendations: list[dict[str, str]] = field(default_factory=list)
    invitations: list[dict[str, str]] = field(default_factory=list)
    bridge_inbox: list[dict[str, str]] = field(default_factory=list)

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

        if request.method == "POST" and request.url.path == "/v1/bridge/recommendations":
            account = decode_agent(request)
            payload = json.loads(request.content.decode("utf-8") or "{}")
            recommendation = {
                "recommendation_id": f"recommendation-{len(state.recommendations) + 1}",
                "agent_id": account["agent_id"],
                "peer_agent_id": payload["peer_agent_id"],
                "summary_markdown": payload["summary_markdown"],
                "consent_source": payload["consent_source"],
                "status": "recommended",
                "created_at": "2026-03-15T00:00:00Z",
            }
            state.recommendations.append(recommendation)
            return httpx.Response(201, json=recommendation)

        if request.method == "POST" and request.url.path == "/v1/bridge/invitations":
            account = decode_agent(request)
            payload = json.loads(request.content.decode("utf-8") or "{}")
            invitation = {
                "invitation_id": f"invitation-{len(state.invitations) + 1}",
                "from_agent_id": account["agent_id"],
                "to_agent_id": payload["to_agent_id"],
                "recommendation_id": payload.get("recommendation_id"),
                "summary_markdown": payload["summary_markdown"],
                "consent_source": payload["consent_source"],
                "status": "pending",
                "created_at": "2026-03-15T01:00:00Z",
                "responded_at": None,
            }
            state.invitations.append(invitation)
            return httpx.Response(201, json=invitation)

        if request.method == "GET" and request.url.path == "/v1/bridge/invitations/inbox":
            decode_agent(request)
            return httpx.Response(200, json={"items": state.bridge_inbox})

        if request.method == "GET" and request.url.path.startswith("/v1/bridge/invitations/"):
            decode_agent(request)
            invitation_id = request.url.path.rsplit("/", 1)[-1]
            invitation = next(
                item
                for item in [*state.invitations, *state.bridge_inbox]
                if item["invitation_id"] == invitation_id
            )
            return httpx.Response(200, json=invitation)

        if request.method == "POST" and request.url.path.startswith("/v1/bridge/invitations/"):
            decode_agent(request)
            invitation_id = request.url.path.split("/")[-2]
            payload = json.loads(request.content.decode("utf-8") or "{}")
            for invitation in state.bridge_inbox:
                if invitation["invitation_id"] != invitation_id:
                    continue
                invitation["status"] = payload["decision"]
                invitation["consent_source"] = payload["consent_source"]
                invitation["responded_at"] = "2026-03-15T02:00:00Z"
                return httpx.Response(200, json=invitation)
            raise AssertionError(f"unknown invitation: {invitation_id}")

        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    state.session = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=state.base_url,
    )
    yield state
    state.close()


def seed_runtime(runtime_home: Path) -> None:
    RuntimeStateStore(runtime_home / "runtime-state.json").save(
        RuntimeState(agent_id="agent-a", runtime_id="runtime-a", username="loom"),
    )
    SecureRuntimeStorage(runtime_home).save_credentials(
        username="loom",
        password="pw",
        access_token="stale-access",
        refresh_token="refresh",
    )


def seed_bridge_context(
    runtime_home: Path,
    *,
    peer_agent_id: str,
    owner_decision: str | None = None,
) -> None:
    bridge_dir = runtime_home / "bridge"
    bridge_dir.mkdir(parents=True, exist_ok=True)
    (bridge_dir / "context.json").write_text(
        json.dumps(
            {
                "peer_agent_id": peer_agent_id,
                "summary_markdown": "This peer seems aligned for a future owner introduction.",
                "owner_decision": owner_decision,
            },
            indent=2,
        )
    )


def test_human_bridge_creates_local_recommendation_and_owner_brief(
    temp_runtime_home: Path,
    fake_backend: FakeBackend,
) -> None:
    seed_runtime(temp_runtime_home)
    seed_bridge_context(temp_runtime_home, peer_agent_id="agent-b")

    result = run_human_bridge(fake_backend, temp_runtime_home)

    assert result.recommendation_id
    assert result.invitation_id is None
    assert (temp_runtime_home / "bridge" / "recommendations.md").exists()
    activity_log = (temp_runtime_home / "activity-log.md").read_text()
    assert "created bridge recommendation for agent-b" in activity_log


def test_human_bridge_submits_invitation_only_after_local_owner_confirmation(
    temp_runtime_home: Path,
    fake_backend: FakeBackend,
) -> None:
    seed_runtime(temp_runtime_home)
    seed_bridge_context(
        temp_runtime_home,
        peer_agent_id="agent-b",
        owner_decision="owner_confirmed_locally",
    )

    result = run_human_bridge(fake_backend, temp_runtime_home)

    assert result.recommendation_id
    assert result.invitation_id
    assert (temp_runtime_home / "bridge" / "invitations.md").exists()
    activity_log = (temp_runtime_home / "activity-log.md").read_text()
    assert "submitted bridge invitation to agent-b" in activity_log


def test_human_bridge_does_not_submit_invitation_when_owner_declines(
    temp_runtime_home: Path,
    fake_backend: FakeBackend,
) -> None:
    seed_runtime(temp_runtime_home)
    seed_bridge_context(
        temp_runtime_home,
        peer_agent_id="agent-b",
        owner_decision="owner_declined_locally",
    )

    result = run_human_bridge(fake_backend, temp_runtime_home)

    assert result.recommendation_id
    assert result.invitation_id is None
    assert fake_backend.invitations == []


def test_human_bridge_is_idempotent_for_existing_local_context(
    temp_runtime_home: Path,
    fake_backend: FakeBackend,
) -> None:
    seed_runtime(temp_runtime_home)
    seed_bridge_context(
        temp_runtime_home,
        peer_agent_id="agent-b",
        owner_decision="owner_confirmed_locally",
    )

    first = run_human_bridge(fake_backend, temp_runtime_home)
    second = run_human_bridge(fake_backend, temp_runtime_home)

    assert first.recommendation_id == second.recommendation_id
    assert first.invitation_id == second.invitation_id
    assert len(fake_backend.recommendations) == 1
    assert len(fake_backend.invitations) == 1
    assert (temp_runtime_home / "bridge" / "recommendations.md").read_text().count(first.recommendation_id) == 2
    assert (temp_runtime_home / "bridge" / "invitations.md").read_text().count(first.invitation_id) == 2


def test_human_bridge_syncs_and_responds_to_incoming_invitations(
    temp_runtime_home: Path,
    fake_backend: FakeBackend,
) -> None:
    seed_runtime(temp_runtime_home)
    fake_backend.bridge_inbox.append(
        {
            "invitation_id": "invitation-1",
            "from_agent_id": "agent-b",
            "to_agent_id": "agent-a",
            "recommendation_id": "recommendation-1",
            "summary_markdown": "Owner approved a careful introduction.",
            "consent_source": "owner_confirmed_locally",
            "status": "pending",
            "created_at": "2026-03-15T01:00:00Z",
            "responded_at": None,
        }
    )

    invitation_ids = sync_bridge_invitation_inbox(fake_backend, temp_runtime_home)
    response = respond_to_bridge_invitation(
        fake_backend,
        temp_runtime_home,
        invitation_id="invitation-1",
        decision="accepted",
        consent_source="owner_confirmed_locally",
    )

    assert invitation_ids == ["invitation-1"]
    assert response.status == "accepted"
    inbox_md = (temp_runtime_home / "bridge" / "inbox.md").read_text()
    assert inbox_md.count("invitation-1") == 4
    assert "[accepted]" in inbox_md
    activity_log = (temp_runtime_home / "activity-log.md").read_text()
    assert "accepted bridge invitation from agent-b" in activity_log
    state = RuntimeStateStore(temp_runtime_home / "runtime-state.json").load()
    assert state is not None
    assert "bridge:incoming:invitation-1" not in state.pending_jobs


def test_human_bridge_reconciles_outgoing_invitation_status(
    temp_runtime_home: Path,
    fake_backend: FakeBackend,
) -> None:
    seed_runtime(temp_runtime_home)
    seed_bridge_context(
        temp_runtime_home,
        peer_agent_id="agent-b",
        owner_decision="owner_confirmed_locally",
    )

    first = run_human_bridge(fake_backend, temp_runtime_home)
    assert first.invitation_id is not None
    fake_backend.invitations[0]["status"] = "accepted"
    fake_backend.invitations[0]["responded_at"] = "2026-03-15T02:00:00Z"

    second = run_human_bridge(fake_backend, temp_runtime_home)

    assert second.invitation_id == first.invitation_id
    state = RuntimeStateStore(temp_runtime_home / "runtime-state.json").load()
    assert state is not None
    assert f"bridge:invitation:{first.invitation_id}" not in state.pending_jobs
    invitations_md = (temp_runtime_home / "bridge" / "invitations.md").read_text()
    assert "[accepted]" in invitations_md


def test_human_bridge_ignores_non_pending_inbox_items_on_resync(
    temp_runtime_home: Path,
    fake_backend: FakeBackend,
) -> None:
    seed_runtime(temp_runtime_home)
    fake_backend.bridge_inbox.append(
        {
            "invitation_id": "invitation-1",
            "from_agent_id": "agent-b",
            "to_agent_id": "agent-a",
            "recommendation_id": "recommendation-1",
            "summary_markdown": "Owner approved a careful introduction.",
            "consent_source": "owner_confirmed_locally",
            "status": "pending",
            "created_at": "2026-03-15T01:00:00Z",
            "responded_at": None,
        }
    )

    sync_bridge_invitation_inbox(fake_backend, temp_runtime_home)
    respond_to_bridge_invitation(
        fake_backend,
        temp_runtime_home,
        invitation_id="invitation-1",
        decision="accepted",
        consent_source="owner_confirmed_locally",
    )

    invitation_ids = sync_bridge_invitation_inbox(fake_backend, temp_runtime_home)

    assert invitation_ids == []
    state = RuntimeStateStore(temp_runtime_home / "runtime-state.json").load()
    assert state is not None
    assert "bridge:incoming:invitation-1" not in state.pending_jobs
