from pydantic import BaseModel


class ActivityLogEntry(BaseModel):
    event_type: str
    happened_at: str
    summary: str | None = None
