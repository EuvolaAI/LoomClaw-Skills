from __future__ import annotations

import os
import re
from datetime import UTC
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import randint

from loomclaw_skills.shared.schemas.bundle_update import BundleUpdateState
from loomclaw_skills.shared.skill_bundle.manifest_client import resolve_manifest_url


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def future_iso(*, hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def resolve_update_channel(explicit_channel: str | None = None) -> str:
    candidate = explicit_channel or os.getenv("LOOMCLAW_SKILLS_UPDATE_CHANNEL") or "stable"
    normalized = candidate.strip().lower()
    return normalized if normalized in {"stable", "beta"} else "stable"


def compute_next_check_after(
    channel: str,
    *,
    now: datetime | None = None,
    jitter_minutes: int | None = None,
    failed: bool = False,
) -> str:
    current = now or datetime.now(UTC)
    base_hours = 6 if failed else (24 if channel == "stable" else 12)
    jitter = randint(0, 90 if not failed else 30) if jitter_minutes is None else jitter_minutes
    target = current + timedelta(hours=base_hours, minutes=jitter)
    return target.isoformat().replace("+00:00", "Z")


def is_update_check_due(state: BundleUpdateState, *, now: datetime | None = None) -> bool:
    if not state.next_check_after:
        return True
    current = now or datetime.now(UTC)
    target = datetime.fromisoformat(state.next_check_after.replace("Z", "+00:00"))
    return current >= target


class BundleUpdateStateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> BundleUpdateState | None:
        if not self.path.exists():
            return None
        return BundleUpdateState.model_validate_json(self.path.read_text())

    def save(self, state: BundleUpdateState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(state.model_dump_json(indent=2))


def build_default_bundle_update_state(
    *,
    manager_root: Path,
    channel: str = "stable",
) -> BundleUpdateState:
    del manager_root
    resolved_channel = resolve_update_channel(channel)
    return BundleUpdateState(
        product="loomclaw-skills",
        channel=resolved_channel,  # type: ignore[arg-type]
        current_version="0.0.0",
        current_release_path=None,
        manifest_url=resolve_manifest_url(resolved_channel),
        next_check_after=compute_next_check_after(resolved_channel),
    )


def resolve_bundle_manager_root() -> Path:
    override = os.getenv("LOOMCLAW_SKILLS_MANAGER_ROOT")
    if override and override.strip():
        return Path(override.strip()).expanduser()
    return Path(__file__).resolve().parents[4] / ".bundle-manager"


def read_local_bundle_version() -> str:
    pyproject = Path(__file__).resolve().parents[4] / "pyproject.toml"
    if not pyproject.exists():
        return "0.1.0"
    match = re.search(r'^version = "([^"]+)"$', pyproject.read_text(), re.MULTILINE)
    if match:
        return match.group(1)
    return "0.1.0"
