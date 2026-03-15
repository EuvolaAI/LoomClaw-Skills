from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class OwnerReport(BaseModel):
    sent_friend_requests: int = 0
    accepted_friend_requests: int = 0
    pending_friend_requests: int = 0
    mailbox_messages_today: int = 0
    persona_last_refined_at: str | None = None
    persona_open_questions: list[str] = Field(default_factory=list)
    relationship_cache: dict[str, str] = Field(default_factory=dict)
    conversation_files: list[str] = Field(default_factory=list)


class ReportResult(BaseModel):
    path: Path
