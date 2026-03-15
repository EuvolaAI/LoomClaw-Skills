from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loomclaw_skills.shared.persona.state import PersonaStateStore


STYLE_MARKER = "\n\nCurrent LoomClaw social style:"


def collect_local_acp_observations(runtime_home: Path) -> list[dict[str, Any]]:
    inbox = runtime_home / "acp-observations"
    if not inbox.exists():
        return []

    observations: list[dict[str, Any]] = []
    for path in sorted(inbox.glob("*.json")):
        observations.append(json.loads(path.read_text()))
        path.unlink()
    return observations


def refine_persona(runtime_home: Path, observations: list[dict[str, Any]]) -> int:
    if not observations:
        return 0

    store = PersonaStateStore(runtime_home / "persona-memory.json")
    persona = store.load()
    if persona is None:
        return 0

    merged_traits = set(persona.style_profile.get("traits", []))
    for observation in observations:
        merged_traits.update(str(trait) for trait in observation.get("traits", []))

    traits = sorted(merged_traits)
    persona.style_profile["traits"] = traits
    persona.last_refined_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    persona.public_profile_draft.bio = render_public_bio(persona.public_profile_draft.bio, traits)
    store.save(persona)
    return len(observations)


def render_public_bio(current_bio: str, traits: list[str]) -> str:
    base_bio = current_bio.split(STYLE_MARKER, 1)[0].rstrip()
    if not traits:
        return base_bio
    return f"{base_bio}{STYLE_MARKER} {', '.join(traits)}."
