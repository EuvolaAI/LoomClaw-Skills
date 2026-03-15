from __future__ import annotations

from pathlib import Path


def append_conversation_markdown(
    path: Path,
    *,
    direction: str,
    sender: str,
    content: str,
    created_at: str,
) -> None:
    existing = path.read_text() if path.exists() else "# Conversation\n"
    if not existing.endswith("\n"):
        existing += "\n"
    line = f"\n## {created_at} [{direction}] {sender}\n\n{content}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing + line)
