from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class PersonaPublicProfileDraft(BaseModel):
    display_name: str
    bio: str


class PersonaState(BaseModel):
    persona_id: str
    persona_mode: Literal["dedicated_persona_agent", "bound_existing_agent"]
    active_agent_ref: str
    public_profile_draft: PersonaPublicProfileDraft
    learning_objectives: list[str] = Field(default_factory=list)
    style_profile: dict[str, list[str]] = Field(default_factory=lambda: {"traits": []})
    last_refined_at: str | None = None
    open_questions: list[str] = Field(default_factory=list)
    local_collaborator_agents: list[str] = Field(default_factory=list)


class PersonaBootstrapResult(BaseModel):
    persona_id: str
    persona_mode: Literal["dedicated_persona_agent", "bound_existing_agent"]
    active_agent_ref: str
    draft_profile: PersonaPublicProfileDraft


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
