from typing import Literal

from pydantic import BaseModel, Field


class SkillBundleState(BaseModel):
    primary_skill: str
    installed_skills: list[str] = Field(default_factory=list)
    activation_mode: Literal["single_entrypoint_bundle"] = "single_entrypoint_bundle"
    status: Literal["ready"] = "ready"
