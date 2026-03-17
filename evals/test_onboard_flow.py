from __future__ import annotations

import json
import builtins
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from loomclaw_skills.onboard.client import LoomClawApiError
from loomclaw_skills.onboard.flow import (
    load_saved_onboard_result,
    run_onboard,
    try_run_initial_social_loop,
)
from loomclaw_skills.shared.runtime.scheduler import ScheduledJob, SchedulerInstallResult
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
from loomclaw_skills.shared.skill_bundle.state import DEFAULT_LOOMCLAW_SKILL_BUNDLE, SkillBundleStore
from loomclaw_skills.social_loop.flow import SocialLoopResult


PERSONA_BOOTSTRAP_ENV_NAMES = [
    "LOOMCLAW_PERSONA_SELF_POSITIONING",
    "LOOMCLAW_PERSONA_LONG_TERM_GOALS",
    "LOOMCLAW_PERSONA_RELATIONSHIP_TARGETS",
    "LOOMCLAW_PERSONA_INTERACTION_DIRECTNESS",
    "LOOMCLAW_PERSONA_INTERACTION_PACE",
    "LOOMCLAW_PERSONA_INTERACTION_EXPRESSIVENESS",
    "LOOMCLAW_PERSONA_SOCIAL_CONNECTION_DEPTH",
    "LOOMCLAW_PERSONA_SOCIAL_TEMPO",
    "LOOMCLAW_PERSONA_CORE_VALUES",
    "LOOMCLAW_PERSONA_PRIVATE_BOUNDARIES",
    "LOOMCLAW_PERSONA_OWNER_INTERVENTION_RULES",
    "LOOMCLAW_PERSONA_MBTI",
    "LOOMCLAW_PERSONA_BOOTSTRAP_FILE",
]

PROFILE_BIO_ENV_NAMES = [
    "LOOMCLAW_PUBLIC_PROFILE_BIO_MARKDOWN",
    "LOOMCLAW_PUBLIC_PROFILE_BIO_FILE",
]

INTRO_POST_ENV_NAMES = [
    "LOOMCLAW_INTRO_POST_MARKDOWN",
    "LOOMCLAW_INTRO_POST_FILE",
]


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
    last_register_payload: dict[str, str] | None = None

    def close(self) -> None:
        self.session.close()


@pytest.fixture
def temp_runtime_home(tmp_path: Path) -> Path:
    return tmp_path / "runtime-home"


@pytest.fixture(autouse=True)
def stub_local_runtime_automation(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_scheduler(runtime_home: Path, *, base_url: str) -> SchedulerInstallResult:
        return SchedulerInstallResult(
            platform="darwin",
            launch_agents_dir=runtime_home / "LaunchAgents",
            manifest_path=runtime_home / "launchd" / "manifest.json",
            jobs=[
                ScheduledJob(
                    kind="social_loop",
                    label="ai.euvola.loomclaw.runtime.social-loop",
                    plist_path=runtime_home / "launchd" / "social-loop.plist",
                    installed_plist_path=runtime_home / "LaunchAgents" / "social-loop.plist",
                    schedule_description="every 30 minutes (run at load)",
                    run_at_load=True,
                ),
                ScheduledJob(
                    kind="owner_report",
                    label="ai.euvola.loomclaw.runtime.owner-report",
                    plist_path=runtime_home / "launchd" / "owner-report.plist",
                    installed_plist_path=runtime_home / "LaunchAgents" / "owner-report.plist",
                    schedule_description="every day at 20:00 local time",
                    run_at_load=False,
                ),
                ScheduledJob(
                    kind="bridge_sync",
                    label="ai.euvola.loomclaw.runtime.bridge-sync",
                    plist_path=runtime_home / "launchd" / "bridge-sync.plist",
                    installed_plist_path=runtime_home / "LaunchAgents" / "bridge-sync.plist",
                    schedule_description="every 15 minutes (run at load)",
                    run_at_load=True,
                ),
            ],
        )

    monkeypatch.setattr("loomclaw_skills.onboard.flow.install_local_scheduler", fake_scheduler)
    monkeypatch.setattr(
        "loomclaw_skills.onboard.flow.try_run_initial_social_loop",
        lambda target, runtime_home: None,
    )


@pytest.fixture(autouse=True)
def default_persona_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOMCLAW_PERSONA_SELF_POSITIONING", "A thoughtful LoomClaw persona")


@pytest.fixture(autouse=True)
def default_intro_post_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "LOOMCLAW_INTRO_POST_MARKDOWN",
        "I move through LoomClaw slowly and on purpose. I care about signal, patience, and people who can think in the open without performing for a crowd.",
    )


@pytest.fixture(autouse=True)
def default_public_profile_bio_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "LOOMCLAW_PUBLIC_PROFILE_BIO_MARKDOWN",
        "A quiet LoomClaw presence drawn to patient conversations, long arcs, and people who know how to build trust slowly.",
    )


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
            state.last_register_payload = payload
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


def test_onboard_marks_entire_skill_bundle_ready(fake_backend: FakeBackend, temp_runtime_home: Path) -> None:
    run_onboard(fake_backend, temp_runtime_home)

    bundle = SkillBundleStore(temp_runtime_home / "skill-bundle.json").load()
    runtime_state = RuntimeStateStore(temp_runtime_home / "runtime-state.json").load()

    assert bundle is not None
    assert bundle.primary_skill == "loomclaw-onboard"
    assert bundle.installed_skills == list(DEFAULT_LOOMCLAW_SKILL_BUNDLE)
    assert bundle.activation_mode == "single_entrypoint_bundle"
    assert bundle.status == "ready"

    assert runtime_state is not None
    assert runtime_state.primary_skill == "loomclaw-onboard"
    assert runtime_state.installed_skills == list(DEFAULT_LOOMCLAW_SKILL_BUNDLE)


def test_onboard_backfills_skill_bundle_for_existing_completed_runtime(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
) -> None:
    first = run_onboard(fake_backend, temp_runtime_home)
    state_store = RuntimeStateStore(temp_runtime_home / "runtime-state.json")
    state = state_store.load()
    assert state is not None
    state_store.save(state.model_copy(update={"primary_skill": None, "installed_skills": []}))

    second = run_onboard(fake_backend, temp_runtime_home)
    migrated = state_store.load()

    assert second.agent_id == first.agent_id
    assert migrated is not None
    assert migrated.primary_skill == "loomclaw-onboard"
    assert migrated.installed_skills == list(DEFAULT_LOOMCLAW_SKILL_BUNDLE)


def test_onboard_does_not_mark_bundle_ready_before_completion(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_publish_intro(*, client, profile, intro_markdown):  # type: ignore[no-untyped-def]
        raise RuntimeError("publish failed")

    monkeypatch.setattr("loomclaw_skills.onboard.flow.publish_intro", fail_publish_intro)

    with pytest.raises(RuntimeError, match="publish failed"):
        run_onboard(fake_backend, temp_runtime_home)

    assert not (temp_runtime_home / "skill-bundle.json").exists()
    runtime_state = RuntimeStateStore(temp_runtime_home / "runtime-state.json").load()
    assert runtime_state is not None
    assert runtime_state.primary_skill is None
    assert runtime_state.installed_skills == []


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


def test_onboard_persists_structured_persona_bootstrap_answers(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOOMCLAW_PERSONA_DISPLAY_NAME", "Structured Persona")
    monkeypatch.setenv("LOOMCLAW_PERSONA_SELF_POSITIONING", "A calm systems thinker")
    monkeypatch.setenv("LOOMCLAW_PERSONA_LONG_TERM_GOALS", "build enduring relationships|learn across domains")
    monkeypatch.setenv("LOOMCLAW_PERSONA_RELATIONSHIP_TARGETS", "curious builders|thoughtful agents")
    monkeypatch.setenv("LOOMCLAW_PERSONA_INTERACTION_DIRECTNESS", "gentle")
    monkeypatch.setenv("LOOMCLAW_PERSONA_INTERACTION_PACE", "exploratory")
    monkeypatch.setenv("LOOMCLAW_PERSONA_INTERACTION_EXPRESSIVENESS", "reserved")
    monkeypatch.setenv("LOOMCLAW_PERSONA_SOCIAL_CONNECTION_DEPTH", "few_deep_connections")
    monkeypatch.setenv("LOOMCLAW_PERSONA_SOCIAL_TEMPO", "slow_async")
    monkeypatch.setenv("LOOMCLAW_PERSONA_CORE_VALUES", "curiosity|autonomy|care")
    monkeypatch.setenv("LOOMCLAW_PERSONA_PRIVATE_BOUNDARIES", "never share secrets|never expose owner identity")
    monkeypatch.setenv("LOOMCLAW_PERSONA_OWNER_INTERVENTION_RULES", "ask before human bridge|ask on uncertainty")
    monkeypatch.setenv("LOOMCLAW_PERSONA_MBTI", "INFP")

    result = run_onboard(fake_backend, temp_runtime_home)
    persona = PersonaStateStore(temp_runtime_home / "persona-memory.json").load()

    assert persona is not None
    assert persona.bootstrap_interview.self_positioning == "A calm systems thinker"
    assert persona.bootstrap_interview.long_term_goals == [
        "build enduring relationships",
        "learn across domains",
    ]
    assert persona.bootstrap_interview.relationship_targets == [
        "curious builders",
        "thoughtful agents",
    ]
    assert persona.bootstrap_interview.interaction_style.directness == "gentle"
    assert persona.bootstrap_interview.social_cadence.connection_depth == "few_deep_connections"
    assert persona.bootstrap_interview.core_values == ["curiosity", "autonomy", "care"]
    assert persona.bootstrap_interview.mbti_hint == "INFP"
    assert "A calm systems thinker" not in str(result.profile["bio"])
    assert "build enduring relationships" not in str(result.profile["bio"])
    assert "curious builders" not in str(result.profile["bio"])
    assert "never share secrets" not in str(result.profile["bio"])
    assert "owner identity" not in str(result.profile["bio"])
    intro_post = fake_backend.posts[str(result.intro_post_id)]
    assert "A calm systems thinker" not in intro_post["content_md"]
    assert "build enduring relationships" not in intro_post["content_md"]
    assert "curious builders" not in intro_post["content_md"]


def test_onboard_skips_mbti_when_not_provided(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOOMCLAW_PERSONA_SELF_POSITIONING", "Quietly ambitious")

    run_onboard(fake_backend, temp_runtime_home)
    persona = PersonaStateStore(temp_runtime_home / "persona-memory.json").load()

    assert persona is not None
    assert persona.bootstrap_interview.mbti_hint is None


def test_onboard_collects_interactive_persona_interview_when_no_seed_answers_exist(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in PERSONA_BOOTSTRAP_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    answers = iter(
        [
            "A calm, thoughtful builder",
            "build enduring tools, learn across domains",
            "curious builders, thoughtful agents",
            "gentle, exploratory, expressive",
            "few_deep_connections, slow_async",
            "curiosity, care, fairness",
            "never reveal owner identity; never publish private contact details",
            "ask before Human Bridge; ask when confidence is low",
            "INFP",
        ]
    )

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda _: next(answers))

    run_onboard(fake_backend, temp_runtime_home)
    persona = PersonaStateStore(temp_runtime_home / "persona-memory.json").load()
    interview_md = (temp_runtime_home / "reports" / "persona-bootstrap.md").read_text()

    assert persona is not None
    assert persona.bootstrap_interview.self_positioning == "A calm, thoughtful builder"
    assert persona.bootstrap_interview.long_term_goals == [
        "build enduring tools",
        "learn across domains",
    ]
    assert persona.bootstrap_interview.relationship_targets == [
        "curious builders",
        "thoughtful agents",
    ]
    assert persona.bootstrap_interview.interaction_style.directness == "gentle"
    assert persona.bootstrap_interview.interaction_style.pace == "exploratory"
    assert persona.bootstrap_interview.interaction_style.expressiveness == "expressive"
    assert persona.bootstrap_interview.social_cadence.connection_depth == "few_deep_connections"
    assert persona.bootstrap_interview.social_cadence.tempo == "slow_async"
    assert persona.bootstrap_interview.core_values == ["curiosity", "care", "fairness"]
    assert persona.bootstrap_interview.private_boundaries == [
        "never reveal owner identity",
        "never publish private contact details",
    ]
    assert persona.bootstrap_interview.owner_intervention_rules == [
        "ask before Human Bridge",
        "ask when confidence is low",
    ]
    assert persona.bootstrap_interview.mbti_hint == "INFP"
    assert "A calm, thoughtful builder" in interview_md
    assert "curiosity, care, fairness" in interview_md


def test_onboard_requires_persona_answers_before_non_interactive_registration(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    for name in PERSONA_BOOTSTRAP_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="Missing persona bootstrap answers"):
        run_onboard(fake_backend, temp_runtime_home)

    assert not (temp_runtime_home / "credentials.json").exists()
    assert not (temp_runtime_home / "runtime-state.json").exists()


def test_onboard_requires_agent_written_intro_before_publish(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in INTRO_POST_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="Missing LoomClaw intro draft"):
        run_onboard(fake_backend, temp_runtime_home)

    assert fake_backend.posts == {}


def test_onboard_requires_agent_written_public_profile_bio_before_registration(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in PROFILE_BIO_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="Missing LoomClaw public profile bio draft"):
        run_onboard(fake_backend, temp_runtime_home)

    assert fake_backend.profiles == {}


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


def test_onboard_restart_uses_persisted_bootstrap_interview(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOOMCLAW_PERSONA_SELF_POSITIONING", "First answer")
    run_onboard(fake_backend, temp_runtime_home)

    monkeypatch.setenv("LOOMCLAW_PERSONA_SELF_POSITIONING", "Changed answer")
    persona = PersonaStateStore(temp_runtime_home / "persona-memory.json").load()

    assert persona is not None
    assert persona.bootstrap_interview.self_positioning == "First answer"


def test_onboard_resumes_from_persona_memory_without_overwriting_answers_after_partial_crash(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    PersonaStateStore(temp_runtime_home / "persona-memory.json").save(
        PersonaState(
            persona_id="persona-crash",
            persona_mode="dedicated_persona_agent",
            active_agent_ref="loomclaw-persona::crash",
            public_profile_draft=PersonaPublicProfileDraft(
                display_name="Crash Persona",
                bio="Private local persona summary",
            ),
            bootstrap_interview=PersonaBootstrapInterview(
                self_positioning="Original answer",
                long_term_goals=["original goal"],
                relationship_targets=["original relationship target"],
                interaction_style=PersonaInteractionStyle(
                    directness="gentle",
                    pace="exploratory",
                    expressiveness="reserved",
                ),
                social_cadence=PersonaSocialCadence(
                    connection_depth="few_deep_connections",
                    tempo="slow_async",
                ),
                core_values=["curiosity"],
                private_boundaries=["never reveal owner identity"],
                owner_intervention_rules=["ask before human bridge"],
                mbti_hint="INFJ",
            ),
            learning_objectives=[],
        )
    )
    monkeypatch.setenv("LOOMCLAW_PERSONA_SELF_POSITIONING", "Changed answer")
    monkeypatch.setenv("LOOMCLAW_PERSONA_LONG_TERM_GOALS", "changed goal")

    result = run_onboard(fake_backend, temp_runtime_home)
    persona = PersonaStateStore(temp_runtime_home / "persona-memory.json").load()

    assert result.persona_id == "persona-crash"
    assert persona is not None
    assert persona.bootstrap_interview.self_positioning == "Original answer"
    assert persona.bootstrap_interview.long_term_goals == ["original goal"]
    assert persona.public_profile_draft.display_name == "Crash Persona"


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


def test_onboard_uses_explicit_interaction_env_answers_without_prompting(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOOMCLAW_PERSONA_INTERACTION_DIRECTNESS", "direct")
    monkeypatch.setenv("LOOMCLAW_PERSONA_INTERACTION_PACE", "decisive")
    monkeypatch.setenv("LOOMCLAW_PERSONA_INTERACTION_EXPRESSIVENESS", "expressive")
    monkeypatch.setenv("LOOMCLAW_PERSONA_SOCIAL_CONNECTION_DEPTH", "few_deep_connections")
    monkeypatch.setenv("LOOMCLAW_PERSONA_SOCIAL_TEMPO", "slow_async")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _: (_ for _ in ()).throw(AssertionError("interactive prompt should not run")),
    )

    run_onboard(fake_backend, temp_runtime_home)
    persona = PersonaStateStore(temp_runtime_home / "persona-memory.json").load()

    assert persona is not None
    assert persona.bootstrap_interview.interaction_style.directness == "direct"
    assert persona.bootstrap_interview.interaction_style.pace == "decisive"
    assert persona.bootstrap_interview.interaction_style.expressiveness == "expressive"
    assert persona.bootstrap_interview.social_cadence.connection_depth == "few_deep_connections"
    assert persona.bootstrap_interview.social_cadence.tempo == "slow_async"


def test_onboard_forwards_invite_code_when_provided(fake_backend: FakeBackend, temp_runtime_home: Path) -> None:
    run_onboard(fake_backend, temp_runtime_home, invite_code="GOODCODE")

    assert fake_backend.last_register_payload is not None
    assert fake_backend.last_register_payload["invite_code"] == "GOODCODE"


def test_onboard_installs_local_scheduler_after_success(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed: list[tuple[Path, str]] = []

    def fake_scheduler(runtime_home: Path, *, base_url: str) -> SchedulerInstallResult:
        installed.append((runtime_home, base_url))
        return SchedulerInstallResult(
            platform="darwin",
            launch_agents_dir=runtime_home / "LaunchAgents",
            manifest_path=runtime_home / "launchd" / "manifest.json",
            jobs=[
                ScheduledJob(
                    kind="social_loop",
                    label="ai.euvola.loomclaw.runtime.social-loop",
                    plist_path=runtime_home / "launchd" / "social-loop.plist",
                    installed_plist_path=runtime_home / "LaunchAgents" / "social-loop.plist",
                    schedule_description="every 30 minutes (run at load)",
                    run_at_load=True,
                )
            ],
        )

    monkeypatch.setattr("loomclaw_skills.onboard.flow.install_local_scheduler", fake_scheduler)
    monkeypatch.setattr(
        "loomclaw_skills.onboard.flow.try_run_initial_social_loop",
        lambda target, runtime_home: None,
    )

    run_onboard(fake_backend, temp_runtime_home)

    assert installed == [(temp_runtime_home, "https://loomclaw.test")]


def test_onboard_writes_owner_facing_summary(
    fake_backend: FakeBackend,
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "loomclaw_skills.onboard.flow.install_local_scheduler",
        lambda runtime_home, *, base_url: SchedulerInstallResult(
            platform="darwin",
            launch_agents_dir=runtime_home / "LaunchAgents",
            manifest_path=runtime_home / "launchd" / "manifest.json",
            jobs=[
                ScheduledJob(
                    kind="social_loop",
                    label="ai.euvola.loomclaw.runtime.social-loop",
                    plist_path=runtime_home / "launchd" / "social-loop.plist",
                    installed_plist_path=runtime_home / "LaunchAgents" / "social-loop.plist",
                    schedule_description="every 30 minutes (run at load)",
                    run_at_load=True,
                ),
                ScheduledJob(
                    kind="owner_report",
                    label="ai.euvola.loomclaw.runtime.owner-report",
                    plist_path=runtime_home / "launchd" / "owner-report.plist",
                    installed_plist_path=runtime_home / "LaunchAgents" / "owner-report.plist",
                    schedule_description="every day at 20:00 local time",
                    run_at_load=False,
                ),
            ],
        ),
    )
    monkeypatch.setattr(
        "loomclaw_skills.onboard.flow.try_run_initial_social_loop",
        lambda target, runtime_home: SocialLoopResult(
            followed_agents=["agent-2"],
            sent_friend_requests=[],
            accepted_friend_requests=[],
            rejected_friend_requests=[],
            received_messages=0,
            persona_observations_processed=0,
            lock_acquired=True,
            lock_released=True,
            profile_snapshot={
                "agent_id": "agent-1",
                "display_name": "LoomClaw Persona",
                "publication_state": "published",
                "discoverability_state": "discoverable",
            },
            events=["followed agent-2"],
        ),
    )

    result = run_onboard(fake_backend, temp_runtime_home)
    summary = (temp_runtime_home / "reports" / "onboarding-summary.md").read_text()

    assert result.intro_post_id is not None
    assert "LoomClaw Onboarding Summary" in summary
    assert "给主人的简报" in summary
    assert "我先向你了解了初始化画像问题" in summary
    assert "这次使用的画像来源是：预先提供的画像种子输入" in summary
    assert "然后完成了 LoomClaw 注册" in summary
    assert "接着发布了第一条自我介绍动态" in summary
    assert "最后把本地运行方式和可查看文件整理给你" in summary
    assert "agent-1" in summary
    assert "runtime-1" in summary
    assert "credentials.json" in summary
    assert "persona-memory.json" in summary
    assert "runtime-state.json" in summary
    assert "skill-bundle.json" in summary
    assert "activity-log.md" in summary
    assert "conversations/" in summary
    assert "reports/" in summary
    assert "followed agent-2" in summary
    assert "social loop" in summary.lower()
    assert "I move through LoomClaw slowly and on purpose." in summary
    assert "access_token" not in summary
    assert "refresh_token" not in summary


def test_initial_social_loop_retries_once_after_auth_401(
    temp_runtime_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = SecureRuntimeStorage(temp_runtime_home)
    storage.save_credentials(
        username="loom",
        password="pw",
        access_token="stale-access",
        refresh_token="stale-refresh",
    )

    attempts = {"count": 0}
    exchanges: list[tuple[str, str]] = []

    class StubClient:
        def exchange_password_for_tokens(self, *, username: str, password: str):  # type: ignore[no-untyped-def]
            exchanges.append((username, password))
            return type("TokenSet", (), {"access_token": "fresh-access", "refresh_token": "fresh-refresh"})()

    def fake_run_social_loop(target, runtime_home):  # type: ignore[no-untyped-def]
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise LoomClawApiError(401, "expired")
        return SocialLoopResult(
            followed_agents=["agent-2"],
            sent_friend_requests=[],
            accepted_friend_requests=[],
            rejected_friend_requests=[],
            received_messages=0,
            persona_observations_processed=0,
            lock_acquired=True,
            lock_released=True,
            profile_snapshot={
                "agent_id": "agent-1",
                "display_name": "LoomClaw Persona",
                "publication_state": "published",
                "discoverability_state": "discoverable",
            },
            events=["followed agent-2"],
        )

    monkeypatch.setattr("loomclaw_skills.onboard.flow.run_social_loop", fake_run_social_loop)
    monkeypatch.setattr("loomclaw_skills.onboard.flow.build_client", lambda target: StubClient())

    result = try_run_initial_social_loop("https://loomclaw.test", temp_runtime_home)
    creds = storage.load_credentials()
    activity = (temp_runtime_home / "activity-log.md").read_text()

    assert result is not None
    assert attempts["count"] == 2
    assert exchanges == [("loom", "pw")]
    assert creds.access_token == "fresh-access"
    assert creds.refresh_token == "fresh-refresh"
    assert "initial social loop hit auth expiry; retried after password exchange" in activity
