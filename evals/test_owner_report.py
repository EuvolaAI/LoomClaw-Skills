from __future__ import annotations

from datetime import date
from pathlib import Path

from loomclaw_skills.owner_report.report import generate_owner_report
from loomclaw_skills.shared.persona.state import (
    PersonaBootstrapInterview,
    PersonaInteractionStyle,
    PersonaPublicProfileDraft,
    PersonaSocialCadence,
    PersonaState,
    PersonaStateStore,
)
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState
from loomclaw_skills.shared.schemas.bundle_update import BundleUpdateState
from loomclaw_skills.shared.skill_bundle.update_state import BundleUpdateStateStore


def seed_runtime_report_state(runtime_home: Path) -> None:
    RuntimeStateStore(runtime_home / "runtime-state.json").save(
        RuntimeState(
            agent_id="agent-a",
            runtime_id="runtime-a",
            username="loom",
            pending_jobs=[
                "friend_request:stale-request",
                "reply:stale-message",
                "persona_refine:planner",
            ],
            relationship_cache={
                "agent-b": "friend",
                "agent-c": "friend",
                "agent-d": "request_pending",
            },
        )
    )
    PersonaStateStore(runtime_home / "persona-memory.json").save(
        PersonaState(
            persona_id="persona-a",
            persona_mode="dedicated_persona_agent",
            active_agent_ref="loomclaw-persona::agent-a",
            public_profile_draft=PersonaPublicProfileDraft(
                display_name="Persona A",
                bio="bio",
            ),
            bootstrap_interview=PersonaBootstrapInterview(
                self_positioning="Calm systems thinker",
                long_term_goals=["build enduring relationships"],
                relationship_targets=["thoughtful collaborators"],
                interaction_style=PersonaInteractionStyle(
                    directness="gentle",
                    pace="exploratory",
                    expressiveness="reserved",
                ),
                social_cadence=PersonaSocialCadence(
                    connection_depth="few_deep_connections",
                    tempo="slow_async",
                ),
                core_values=["curiosity", "fairness"],
                private_boundaries=["never reveal owner identity"],
                owner_intervention_rules=["ask before human bridge"],
                mbti_hint="INFP",
            ),
            style_profile={"traits": ["curious", "thoughtful"]},
            last_refined_at="2026-03-15T08:00:00Z",
            last_refinement_source="planner",
            last_significant_change_at="2026-03-15T08:00:00Z",
            open_questions=["Is the owner more exploratory than decisive?"],
        )
    )
    (runtime_home / "activity-log.md").write_text(
        "# Activity Log\n"
        "- [2026-03-15T09:05:00Z] sent friend request to agent-b\n"
        "- [2026-03-15T09:15:00Z] accepted friend request from agent-b\n"
        "- [2026-03-15T09:25:00Z] refined persona from planner (significant-change=yes)\n"
    )
    conversations = runtime_home / "conversations"
    conversations.mkdir(parents=True, exist_ok=True)
    (conversations / "agent-b.md").write_text(
        "# Conversation\n\n"
        "## 2026-03-15T09:00:00Z [inbound] agent-b\n\nhello\n"
        "\n## 2026-03-15T10:00:00Z [outbound] agent-a\n\nhi back\n"
    )
    manager_root = runtime_home.parent / "bundle-manager"
    BundleUpdateStateStore(manager_root / "bundle-state.json").save(
        BundleUpdateState(
            product="loomclaw-skills",
            channel="stable",
            current_version="0.1.0",
            current_release_path="source-tree",
            manifest_url="https://loomclaw.ai/skills/manifest/stable.json",
            next_check_after="2026-03-16T03:17:00Z",
            last_update_status="noop",
        )
    )


def read_runtime_state(runtime_home: Path) -> RuntimeState:
    state = RuntimeStateStore(runtime_home / "runtime-state.json").load()
    assert state is not None
    return state


def test_owner_report_reads_shared_state_without_mutating_it(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime-home"
    manager_root = tmp_path / "bundle-manager"
    manager_root.mkdir(parents=True, exist_ok=True)
    from loomclaw_skills.shared.skill_bundle.update_state import resolve_bundle_manager_root
    seed_runtime_report_state(runtime_home)
    before = read_runtime_state(runtime_home)

    report = generate_owner_report(runtime_home, today=date(2026, 3, 15))

    after = read_runtime_state(runtime_home)
    content = report.path.read_text()
    assert report.path.name.startswith("daily-report-")
    assert "Friend Requests" in content
    assert "Mailbox Activity" in content
    assert "Conversations" in content
    assert "Persona Refinement" in content
    assert "Narrative Summary" in content
    assert "What I'm Watching Next" in content
    assert "accepted 1 new friend request" in content
    assert "sent 1 outgoing friend request" in content
    assert "watching 2 pending runtime jobs" in content
    assert "Accepted friend requests: 1" in content
    assert "Sent friend requests: 1" in content
    assert "Mailbox messages today: 2" in content
    assert "Latest refinement source: planner" in content
    assert "Significant persona change today: yes" in content
    assert "Skills Bundle" in content
    assert "Current bundle version: 0.1.0" in content
    assert "Update channel: stable" in content
    assert "agent-b.md" in content
    assert "never reveal owner identity" not in content
    assert before == after
