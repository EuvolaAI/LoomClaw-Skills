from __future__ import annotations

from pathlib import Path

from loomclaw_skills.shared.persona.state import (
    PersonaBootstrapInterview,
    PersonaPublicProfileDraft,
    PersonaState,
    PersonaStateStore,
)
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState
from loomclaw_skills.social_loop.persona_learning import (
    collect_local_acp_observations,
    import_shared_acp_responses,
    queue_local_acp_observation_requests,
    refine_persona,
    respond_to_local_acp_requests,
)


def seed_runtime(runtime_home: Path, *, agent_id: str) -> None:
    RuntimeStateStore(runtime_home / "runtime-state.json").save(
        RuntimeState(agent_id=agent_id, runtime_id=f"runtime-{agent_id}", username=f"user-{agent_id}")
    )


def seed_persona(
    runtime_home: Path,
    *,
    agent_id: str,
    collaborators: list[str],
    display_name: str,
    bio: str,
    traits: list[str],
    goals: list[str],
) -> None:
    PersonaStateStore(runtime_home / "persona-memory.json").save(
        PersonaState(
            persona_id=f"persona-{agent_id}",
            persona_mode="dedicated_persona_agent",
            active_agent_ref=f"loomclaw-persona::{agent_id}",
            public_profile_draft=PersonaPublicProfileDraft(display_name=display_name, bio=bio),
            bootstrap_interview=PersonaBootstrapInterview(
                self_positioning=bio,
                long_term_goals=goals,
                private_boundaries=["never reveal owner identity"],
            ),
            local_collaborator_agents=collaborators,
            style_profile={"traits": traits},
        )
    )


def test_persona_learning_moves_acp_requests_through_shared_exchange(tmp_path: Path, monkeypatch) -> None:
    exchange_root = tmp_path / "acp-exchange"
    monkeypatch.setenv("LOOMCLAW_ACP_EXCHANGE_ROOT", str(exchange_root))

    requester_home = tmp_path / "runtime-a"
    responder_home = tmp_path / "runtime-b"
    seed_runtime(requester_home, agent_id="agent-a")
    seed_runtime(responder_home, agent_id="agent-b")
    seed_persona(
        requester_home,
        agent_id="agent-a",
        collaborators=["agent-b"],
        display_name="Requester",
        bio="A patient builder looking for thoughtful collaborators.",
        traits=[],
        goals=["build enduring tools"],
    )
    seed_persona(
        responder_home,
        agent_id="agent-b",
        collaborators=[],
        display_name="Responder",
        bio="A reflective operator who values trust and long-term momentum.",
        traits=["reflective", "trust-oriented"],
        goals=["build durable relationships"],
    )

    requested = queue_local_acp_observation_requests(requester_home)
    responded = respond_to_local_acp_requests(responder_home)
    imported = import_shared_acp_responses(requester_home)
    observations = collect_local_acp_observations(requester_home)
    refinement = refine_persona(requester_home, observations)
    persona = PersonaStateStore(requester_home / "persona-memory.json").load()

    assert requested == ["agent-b"]
    assert responded == 1
    assert imported == 1
    assert len(observations) == 1
    assert refinement.processed_count == 1
    assert persona is not None
    assert "reflective" in persona.style_profile["traits"]
    assert "trust-oriented" in persona.style_profile["traits"]
    assert (exchange_root / "requests" / "processed").exists()
    assert (exchange_root / "responses" / "processed").exists()
