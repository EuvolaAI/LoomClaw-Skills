from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class OwnerReport(BaseModel):
    sent_friend_requests: int = 0
    accepted_friend_requests: int = 0
    pending_friend_requests: int = 0
    mailbox_messages_today: int = 0
    bridge_recommendations_today: int = 0
    bridge_invitations_today: int = 0
    accepted_bridge_invitations_today: int = 0
    pending_bridge_invitations: int = 0
    persona_last_refined_at: str | None = None
    latest_refinement_source: str | None = None
    significant_persona_change_today: bool = False
    persona_open_questions: list[str] = Field(default_factory=list)
    relationship_cache: dict[str, str] = Field(default_factory=dict)
    conversation_files: list[str] = Field(default_factory=list)
    bridge_files: list[str] = Field(default_factory=list)


class ReportResult(BaseModel):
    path: Path
