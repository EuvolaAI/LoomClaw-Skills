from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from loomclaw_skills.shared.persona.state import PersonaObservationSummary, PersonaStateStore


STYLE_MARKER = "\n\nCurrent LoomClaw social style:"


@dataclass(frozen=True, slots=True)
class ObservationEnvelope:
    observation: PersonaObservationSummary
    path: Path


@dataclass(frozen=True, slots=True)
class PersonaRefinementOutcome:
    processed_count: int
    sources: list[str]
    significant_change: bool


def collect_local_acp_observations(runtime_home: Path) -> list[ObservationEnvelope]:
    inbox = runtime_home / "acp-observations"
    if not inbox.exists():
        return []

    persona = PersonaStateStore(runtime_home / "persona-memory.json").load()
    if persona is None:
        return []

    allowed_sources = set(persona.local_collaborator_agents)
    observations: list[ObservationEnvelope] = []
    for path in sorted(inbox.glob("*.json")):
        try:
            observation = PersonaObservationSummary.model_validate(json.loads(path.read_text()))
        except (ValidationError, json.JSONDecodeError):
            archive_observation(path, bucket="rejected")
            continue
        if observation.source_agent_id not in allowed_sources:
            archive_observation(path, bucket="rejected")
            continue
        observations.append(ObservationEnvelope(observation=observation, path=path))
    return observations


def refine_persona(runtime_home: Path, observations: list[ObservationEnvelope]) -> PersonaRefinementOutcome:
    if not observations:
        return PersonaRefinementOutcome(processed_count=0, sources=[], significant_change=False)

    store = PersonaStateStore(runtime_home / "persona-memory.json")
    persona = store.load()
    if persona is None:
        return PersonaRefinementOutcome(processed_count=0, sources=[], significant_change=False)

    previous_traits = set(persona.style_profile.get("traits", []))
    merged_traits = set(previous_traits)
    for envelope in observations:
        merged_traits.update(str(trait) for trait in envelope.observation.traits)

    traits = sorted(merged_traits)
    significant_change = traits != sorted(previous_traits)
    refined_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    persona.style_profile["traits"] = traits
    persona.last_refined_at = refined_at
    persona.last_refinement_source = observations[-1].observation.source_agent_id
    if significant_change:
        persona.last_significant_change_at = refined_at
    persona.observation_summaries.extend([envelope.observation for envelope in observations])
    persona.public_profile_draft.bio = render_public_bio(persona.public_profile_draft.bio, traits)
    store.save(persona)
    for envelope in observations:
        archive_observation(envelope.path, bucket="processed")
    sources = [envelope.observation.source_agent_id for envelope in observations]
    return PersonaRefinementOutcome(
        processed_count=len(observations),
        sources=sources,
        significant_change=significant_change,
    )


def render_public_bio(current_bio: str, traits: list[str]) -> str:
    base_bio = current_bio.split(STYLE_MARKER, 1)[0].rstrip()
    if not traits:
        return base_bio
    return f"{base_bio}{STYLE_MARKER} {', '.join(traits)}."


def archive_observation(path: Path, *, bucket: str) -> None:
    target_dir = path.parent / bucket
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / path.name
    if target_path.exists():
        target_path.unlink()
    path.replace(target_path)
