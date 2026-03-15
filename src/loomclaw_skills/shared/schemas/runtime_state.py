from pydantic import BaseModel, Field


class RuntimeState(BaseModel):
    agent_id: str
    runtime_id: str
    username: str
    feed_cursor: str | None = None
    pending_jobs: list[str] = Field(default_factory=list)
    retry_queue: list[str] = Field(default_factory=list)
    relationship_cache: dict[str, str] = Field(default_factory=dict)
