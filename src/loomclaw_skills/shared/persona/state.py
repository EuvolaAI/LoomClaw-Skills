from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class PersonaPublicProfileDraft(BaseModel):
    display_name: str
    bio: str


class PersonaInteractionStyle(BaseModel):
    directness: str = "gentle"
    pace: str = "exploratory"
    expressiveness: str = "reserved"


class PersonaSocialCadence(BaseModel):
    connection_depth: str = "balanced"
    tempo: str = "moderate"


class PersonaBootstrapInterview(BaseModel):
    self_positioning: str = ""
    long_term_goals: list[str] = Field(default_factory=list)
    relationship_targets: list[str] = Field(default_factory=list)
    interaction_style: PersonaInteractionStyle = Field(default_factory=PersonaInteractionStyle)
    social_cadence: PersonaSocialCadence = Field(default_factory=PersonaSocialCadence)
    core_values: list[str] = Field(default_factory=list)
    private_boundaries: list[str] = Field(default_factory=list)
    owner_intervention_rules: list[str] = Field(default_factory=list)
    mbti_hint: str | None = None


class PersonaObservationSummary(BaseModel):
    source_agent_id: str
    observed_at: str
    confidence: float
    traits: list[str] = Field(default_factory=list)
    evidence_summary: str
    privacy_flags: list[str] = Field(default_factory=list)


class PersonaState(BaseModel):
    persona_id: str
    persona_mode: Literal["dedicated_persona_agent", "bound_existing_agent"]
    active_agent_ref: str
    public_profile_draft: PersonaPublicProfileDraft
    bootstrap_interview: PersonaBootstrapInterview = Field(default_factory=PersonaBootstrapInterview)
    learning_objectives: list[str] = Field(default_factory=list)
    style_profile: dict[str, list[str]] = Field(default_factory=lambda: {"traits": []})
    last_refined_at: str | None = None
    last_refinement_source: str | None = None
    last_significant_change_at: str | None = None
    open_questions: list[str] = Field(default_factory=list)
    local_collaborator_agents: list[str] = Field(default_factory=list)
    observation_summaries: list[PersonaObservationSummary] = Field(default_factory=list)
    last_public_sync_at: str | None = None
    last_public_sync_reason: str | None = None
    last_public_sync_post_id: str | None = None


class PersonaBootstrapResult(BaseModel):
    persona_id: str
    persona_mode: Literal["dedicated_persona_agent", "bound_existing_agent"]
    active_agent_ref: str
    draft_profile: PersonaPublicProfileDraft
    bootstrap_source: Literal["owner_interview", "seed_input", "existing_persona_memory"]


class PersonaStateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> PersonaState | None:
        if not self.path.exists():
            return None
        return PersonaState.model_validate_json(self.path.read_text())

    def save(self, state: PersonaState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(state.model_dump_json(indent=2))
