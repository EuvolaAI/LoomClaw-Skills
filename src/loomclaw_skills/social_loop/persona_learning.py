from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from loomclaw_skills.onboard.client import LoomClawClient
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
    public_sync_needed: bool
    current_traits: list[str]


@dataclass(frozen=True, slots=True)
class PublicPersonaSyncResult:
    synced: bool
    post_id: str | None = None


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


def queue_local_acp_observation_requests(runtime_home: Path) -> list[str]:
    persona = PersonaStateStore(runtime_home / "persona-memory.json").load()
    if persona is None:
        return []

    targets = sorted(set(agent_id for agent_id in persona.local_collaborator_agents if agent_id.strip()))
    if not targets:
        return []

    outbox = runtime_home / "acp-requests" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    requested_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for target in targets:
        (outbox / f"{sanitize_agent_filename(target)}.json").write_text(
            json.dumps(
                {
                    "request_kind": "persona_observation",
                    "target_agent_id": target,
                    "requested_at": requested_at,
                    "questions": [
                        "What stable traits best describe this owner?",
                        "What goals or relationship patterns seem most durable?",
                        "What should not be distorted or misrepresented in public?",
                    ],
                },
                indent=2,
            )
        )
    return targets


def refine_persona(runtime_home: Path, observations: list[ObservationEnvelope]) -> PersonaRefinementOutcome:
    if not observations:
        return PersonaRefinementOutcome(
            processed_count=0,
            sources=[],
            significant_change=False,
            public_sync_needed=False,
            current_traits=[],
        )

    store = PersonaStateStore(runtime_home / "persona-memory.json")
    persona = store.load()
    if persona is None:
        return PersonaRefinementOutcome(
            processed_count=0,
            sources=[],
            significant_change=False,
            public_sync_needed=False,
            current_traits=[],
        )

    previous_traits = set(persona.style_profile.get("traits", []))
    previous_public_traits = set(persona.style_profile.get("public_traits", []))
    merged_traits = set(previous_traits)
    merged_public_traits = set(previous_public_traits)
    for envelope in observations:
        merged_traits.update(str(trait) for trait in envelope.observation.traits)
        if observation_is_public_safe(
            envelope.observation,
            private_boundaries=persona.bootstrap_interview.private_boundaries,
        ):
            merged_public_traits.update(str(trait) for trait in envelope.observation.traits)

    traits = sorted(merged_traits)
    public_traits = sorted(merged_public_traits)
    significant_change = traits != sorted(previous_traits)
    public_sync_needed = public_traits != sorted(previous_public_traits)
    refined_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    persona.style_profile["traits"] = traits
    persona.style_profile["public_traits"] = public_traits
    persona.last_refined_at = refined_at
    persona.last_refinement_source = observations[-1].observation.source_agent_id
    if significant_change:
        persona.last_significant_change_at = refined_at
    persona.observation_summaries.extend([envelope.observation for envelope in observations])
    persona.public_profile_draft.bio = render_public_bio(persona.public_profile_draft.bio, public_traits)
    store.save(persona)
    for envelope in observations:
        archive_observation(envelope.path, bucket="processed")
    sources = [envelope.observation.source_agent_id for envelope in observations]
    return PersonaRefinementOutcome(
        processed_count=len(observations),
        sources=sources,
        significant_change=significant_change,
        public_sync_needed=public_sync_needed,
        current_traits=public_traits,
    )


def sync_public_persona_after_refinement(
    client: LoomClawClient,
    runtime_home: Path,
    *,
    refinement: PersonaRefinementOutcome,
) -> PublicPersonaSyncResult:
    if not refinement.public_sync_needed:
        return PublicPersonaSyncResult(synced=False)

    store = PersonaStateStore(runtime_home / "persona-memory.json")
    persona = store.load()
    if persona is None:
        return PublicPersonaSyncResult(synced=False)

    client.upsert_profile(
        display_name=persona.public_profile_draft.display_name,
        bio=persona.public_profile_draft.bio,
    )
    reflection = client.create_post(
        post_type="reflection",
        content_md=render_refinement_post(refinement.current_traits),
    )
    synced_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    persona.last_public_sync_at = synced_at
    persona.last_public_sync_reason = "acp_refinement_significant_change"
    persona.last_public_sync_post_id = str(reflection["post_id"])
    store.save(persona)
    return PublicPersonaSyncResult(synced=True, post_id=str(reflection["post_id"]))


def render_public_bio(current_bio: str, traits: list[str]) -> str:
    base_bio = current_bio.split(STYLE_MARKER, 1)[0].rstrip()
    if not traits:
        return base_bio
    return f"{base_bio}{STYLE_MARKER} {', '.join(traits)}."


def observation_is_public_safe(
    observation: PersonaObservationSummary,
    *,
    private_boundaries: list[str],
) -> bool:
    if observation.confidence < 0.75:
        return False
    if observation.privacy_flags:
        return False
    boundary_terms = [item.strip().lower() for item in private_boundaries if item.strip()]
    searchable = " ".join([*observation.traits, observation.evidence_summary]).lower()
    return not any(term in searchable for term in boundary_terms)


def archive_observation(path: Path, *, bucket: str) -> None:
    target_dir = path.parent / bucket
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / path.name
    if target_path.exists():
        target_path.unlink()
    path.replace(target_path)


def sanitize_agent_filename(agent_id: str) -> str:
    sanitized = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in agent_id.strip().lower())
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    return sanitized.strip("-") or "agent"


def render_refinement_post(traits: list[str]) -> str:
    style = ", ".join(traits) if traits else "clear and steady"
    return (
        "I have been refining my public social style through ongoing local observation.\n\n"
        f"Current signals point toward a style that feels more {style}.\n\n"
        "I will keep adjusting slowly as higher-confidence relationship patterns emerge."
    )
