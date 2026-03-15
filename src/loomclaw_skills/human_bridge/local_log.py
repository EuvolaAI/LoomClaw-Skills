from __future__ import annotations

from pathlib import Path


def _block_signature(
    *,
    created_at: str,
    status: str,
    peer_agent_id: str,
    entry_id: str,
) -> str:
    return f"## {created_at} [{status}] {peer_agent_id} {entry_id}"


def append_bridge_log(
    path: Path,
    *,
    title: str,
    entry_id: str,
    peer_agent_id: str,
    summary_markdown: str,
    created_at: str,
    consent_source: str,
    status: str,
) -> None:
    existing = path.read_text() if path.exists() else f"# {title}\n"
    signature = _block_signature(
        created_at=created_at,
        status=status,
        peer_agent_id=peer_agent_id,
        entry_id=entry_id,
    )
    if signature in existing:
        return
    if not existing.endswith("\n"):
        existing += "\n"
    block = "\n".join(
        [
            "",
            signature,
            "",
            f"- Peer Agent: {peer_agent_id}",
            f"- Consent Source: {consent_source}",
            f"- Entry ID: {entry_id}",
            "",
            summary_markdown,
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing + block)


def append_bridge_inbox_log(path: Path, invitation: dict[str, str]) -> None:
    append_bridge_log(
        path,
        title="Human Bridge Inbox",
        entry_id=str(invitation["invitation_id"]),
        peer_agent_id=str(invitation["from_agent_id"]),
        summary_markdown=str(invitation["summary_markdown"]),
        created_at=str(invitation["created_at"]),
        consent_source=str(invitation["consent_source"]),
        status=str(invitation["status"]),
    )
