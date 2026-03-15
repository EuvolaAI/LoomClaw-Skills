from __future__ import annotations

from datetime import date
from pathlib import Path

from loomclaw_skills.owner_report.report import generate_owner_report
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState


def seed_runtime_with_bridge(runtime_home: Path) -> None:
    RuntimeStateStore(runtime_home / "runtime-state.json").save(
        RuntimeState(
            agent_id="agent-a",
            runtime_id="runtime-a",
            username="loom",
            pending_jobs=[
                "bridge:invitation:invitation-1",
                "bridge:incoming:invitation-2",
            ],
            relationship_cache={"agent-b": "friend"},
        )
    )
    (runtime_home / "activity-log.md").write_text(
        "# Activity Log\n"
        "- [2026-03-15T10:00:00Z] created bridge recommendation for agent-b\n"
        "- [2026-03-15T10:05:00Z] submitted bridge invitation to agent-b\n"
        "- [2026-03-15T10:45:00Z] accepted bridge invitation from agent-b\n"
    )
    bridge_dir = runtime_home / "bridge"
    bridge_dir.mkdir(parents=True, exist_ok=True)
    (bridge_dir / "recommendations.md").write_text("# Bridge Recommendations\n")
    (bridge_dir / "invitations.md").write_text("# Bridge Invitations\n")
    (bridge_dir / "inbox.md").write_text("# Bridge Inbox\n")


def test_owner_report_includes_human_bridge_section(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime-home"
    seed_runtime_with_bridge(runtime_home)

    report = generate_owner_report(runtime_home, today=date(2026, 3, 15))

    content = report.path.read_text()
    assert "Human Bridge" in content
    assert "Bridge recommendations today: 1" in content
    assert "Pending bridge invitations: 2" in content
    assert "Accepted bridge invitations today: 1" in content
    assert "recommendations.md" in content
    assert "invitations.md" in content
    assert "inbox.md" in content
