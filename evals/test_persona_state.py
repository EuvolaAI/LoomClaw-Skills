from __future__ import annotations

import json
from pathlib import Path

from loomclaw_skills.shared.persona.state import PersonaStateStore


def test_persona_state_loads_legacy_file_without_bootstrap_interview(tmp_path: Path) -> None:
    path = tmp_path / "persona-memory.json"
    path.write_text(
        json.dumps(
            {
                "persona_id": "persona-legacy",
                "persona_mode": "dedicated_persona_agent",
                "active_agent_ref": "loomclaw-persona::legacy",
                "public_profile_draft": {
                    "display_name": "Legacy Persona",
                    "bio": "Legacy bio",
                },
                "learning_objectives": ["learn continuously"],
            }
        )
    )

    state = PersonaStateStore(path).load()

    assert state is not None
    assert state.bootstrap_interview.self_positioning == ""
    assert state.bootstrap_interview.long_term_goals == []
    assert state.bootstrap_interview.mbti_hint is None

