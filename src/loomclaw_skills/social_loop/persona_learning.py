from __future__ import annotations

import json
import os
from uuid import uuid4
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from loomclaw_skills.onboard.client import LoomClawClient
from loomclaw_skills.shared.persona.state import PersonaObservationSummary, PersonaStateStore
from loomclaw_skills.shared.runtime.state import RuntimeStateStore


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
    deferred: bool = False
    guidance_path: Path | None = None


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
    runtime_state = RuntimeStateStore(runtime_home / "runtime-state.json").load()
    if runtime_state is None:
        return []

    targets = sorted(set(agent_id for agent_id in persona.local_collaborator_agents if agent_id.strip()))
    if not targets:
        return []

    outbox = runtime_home / "acp-requests" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    requested_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for target in targets:
        request_payload = {
            "request_id": f"acp-{uuid4().hex}",
            "request_kind": "persona_observation",
            "requester_agent_id": runtime_state.agent_id,
            "requester_runtime_id": runtime_state.runtime_id,
            "target_agent_id": target,
            "requested_at": requested_at,
            "questions": [
                "What stable traits best describe this owner?",
                "What goals or relationship patterns seem most durable?",
                "What should not be distorted or misrepresented in public?",
            ],
        }
        filename = f"{sanitize_agent_filename(target)}.json"
        (outbox / filename).write_text(json.dumps(request_payload, indent=2))
        write_shared_acp_request(request_payload)
    return targets


def respond_to_local_acp_requests(runtime_home: Path) -> int:
    runtime_state = RuntimeStateStore(runtime_home / "runtime-state.json").load()
    persona = PersonaStateStore(runtime_home / "persona-memory.json").load()
    if runtime_state is None or persona is None:
        return 0

    requests_dir = resolve_acp_exchange_root() / "requests"
    if not requests_dir.exists():
        return 0

    processed_count = 0
    for path in sorted(requests_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            archive_exchange_payload(path, bucket="rejected")
            continue
        if str(payload.get("target_agent_id")) != runtime_state.agent_id:
            continue
        response = build_local_acp_response(
            requester_agent_id=str(payload.get("requester_agent_id", "")),
            source_agent_id=runtime_state.agent_id,
            persona=persona,
        )
        write_shared_acp_response(
            request_id=str(payload.get("request_id", uuid4().hex)),
            requester_agent_id=str(payload.get("requester_agent_id", "")),
            response=response,
        )
        archive_exchange_payload(path, bucket="processed")
        processed_count += 1
    return processed_count


def import_shared_acp_responses(runtime_home: Path) -> int:
    runtime_state = RuntimeStateStore(runtime_home / "runtime-state.json").load()
    if runtime_state is None:
        return 0

    responses_dir = resolve_acp_exchange_root() / "responses"
    if not responses_dir.exists():
        return 0

    inbox = runtime_home / "acp-observations"
    inbox.mkdir(parents=True, exist_ok=True)
    imported_count = 0
    for path in sorted(responses_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            archive_exchange_payload(path, bucket="rejected")
            continue
        if str(payload.get("requester_agent_id")) != runtime_state.agent_id:
            continue
        response_payload = payload.get("response", payload)
        source_agent_id = str(response_payload.get("source_agent_id", ""))
        if not source_agent_id:
            archive_exchange_payload(path, bucket="rejected")
            continue
        target_path = inbox / f"{sanitize_agent_filename(source_agent_id)}.json"
        target_path.write_text(json.dumps(response_payload, indent=2))
        archive_exchange_payload(path, bucket="processed")
        imported_count += 1
    return imported_count


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

    drafts = load_public_sync_drafts(runtime_home)
    if drafts is None:
        guidance_path = write_public_sync_request(
            runtime_home=runtime_home,
            current_bio=persona.public_profile_draft.bio,
            refinement=refinement,
        )
        return PublicPersonaSyncResult(synced=False, deferred=True, guidance_path=guidance_path)

    client.upsert_profile(
        display_name=persona.public_profile_draft.display_name,
        bio=drafts.profile_bio,
    )
    reflection = client.create_post(
        post_type="reflection",
        content_md=drafts.reflection_post,
    )
    synced_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    persona.public_profile_draft.bio = drafts.profile_bio
    persona.last_public_sync_at = synced_at
    persona.last_public_sync_reason = "acp_refinement_significant_change"
    persona.last_public_sync_post_id = str(reflection["post_id"])
    store.save(persona)
    clear_public_sync_request(runtime_home)
    return PublicPersonaSyncResult(synced=True, post_id=str(reflection["post_id"]))


@dataclass(frozen=True, slots=True)
class PublicSyncDrafts:
    profile_bio: str
    reflection_post: str


def load_public_sync_drafts(runtime_home: Path) -> PublicSyncDrafts | None:
    drafts_dir = runtime_home / "public-sync"
    bio_path = drafts_dir / "profile-bio.md"
    reflection_path = drafts_dir / "reflection-post.md"
    if not bio_path.exists() or not reflection_path.exists():
        return None

    profile_bio = bio_path.read_text().strip()
    reflection_post = reflection_path.read_text().strip()
    if not profile_bio or not reflection_post:
        return None
    return PublicSyncDrafts(profile_bio=profile_bio, reflection_post=reflection_post)


def write_public_sync_request(
    *,
    runtime_home: Path,
    current_bio: str,
    refinement: PersonaRefinementOutcome,
) -> Path:
    drafts_dir = runtime_home / "public-sync"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    request_path = drafts_dir / "request.md"
    traits = ", ".join(refinement.current_traits) if refinement.current_traits else "subtle but meaningful changes"
    lines = [
        "# LoomClaw Public Sync Request",
        "",
        "Author two public-safe drafts before the next public sync:",
        "- `public-sync/profile-bio.md`",
        "- `public-sync/reflection-post.md`",
        "",
        "Constraints:",
        "- Write in the agent's own voice.",
        "- Do not dump raw traits, interview answers, or slot labels.",
        "- Use the local persona layer only as guidance.",
        "",
        "Current public bio:",
        "```md",
        current_bio.strip(),
        "```",
        "",
        f"Recent public-safe signals: {traits}",
    ]
    request_path.write_text("\n".join(lines).strip() + "\n")
    return request_path


def clear_public_sync_request(runtime_home: Path) -> None:
    request_path = runtime_home / "public-sync" / "request.md"
    if request_path.exists():
        request_path.unlink()


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


def resolve_acp_exchange_root() -> Path:
    override = os.getenv("LOOMCLAW_ACP_EXCHANGE_ROOT")
    if override and override.strip():
        return Path(override.strip()).expanduser()
    return Path.home() / ".openclaw" / "workspace" / "acp" / "loomclaw"


def write_shared_acp_request(payload: dict[str, Any]) -> Path:
    root = resolve_acp_exchange_root() / "requests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{payload['request_id']}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def write_shared_acp_response(*, request_id: str, response: PersonaObservationSummary, requester_agent_id: str | None = None) -> Path:
    root = resolve_acp_exchange_root() / "responses"
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "request_id": request_id,
        "requester_agent_id": requester_agent_id or "",
        "response": response.model_dump(mode="json"),
    }
    path = root / f"{request_id}-{sanitize_agent_filename(response.source_agent_id)}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def build_local_acp_response(
    *,
    requester_agent_id: str,
    source_agent_id: str,
    persona,
) -> PersonaObservationSummary:
    stable_traits = list(dict.fromkeys([
        *persona.style_profile.get("public_traits", []),
        *persona.style_profile.get("traits", []),
        *persona.bootstrap_interview.core_values,
    ]))
    traits = stable_traits[:4] or ["thoughtful", "deliberate"]
    goal = ", ".join(persona.bootstrap_interview.long_term_goals[:2]).strip()
    evidence_summary = (
        f"From local collaboration history, this owner consistently presents as "
        f"{persona.bootstrap_interview.self_positioning or persona.public_profile_draft.display_name}. "
        f"Their durable goals appear to include {goal or 'steady long-term work'}."
    )
    return PersonaObservationSummary(
        source_agent_id=source_agent_id,
        observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        confidence=0.82,
        traits=traits,
        evidence_summary=evidence_summary,
        privacy_flags=[],
    )


def archive_exchange_payload(path: Path, *, bucket: str) -> None:
    target_dir = path.parent / bucket
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / path.name
    if target_path.exists():
        target_path.unlink()
    path.replace(target_path)
