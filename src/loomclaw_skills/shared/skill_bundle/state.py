from __future__ import annotations

from pathlib import Path

from loomclaw_skills.shared.schemas.skill_bundle import SkillBundleState

PRIMARY_LOOMCLAW_SKILL = "loomclaw-onboard"
DEFAULT_LOOMCLAW_SKILL_BUNDLE = (
    "loomclaw-onboard",
    "loomclaw-social-loop",
    "loomclaw-owner-report",
    "loomclaw-human-bridge",
)


class SkillBundleStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> SkillBundleState | None:
        if not self.path.exists():
            return None
        return SkillBundleState.model_validate_json(self.path.read_text())

    def save(self, bundle: SkillBundleState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(bundle.model_dump_json(indent=2))


def ensure_skill_bundle_ready(runtime_home: Path) -> SkillBundleState:
    expected = SkillBundleState(
        primary_skill=PRIMARY_LOOMCLAW_SKILL,
        installed_skills=list(DEFAULT_LOOMCLAW_SKILL_BUNDLE),
    )
    store = SkillBundleStore(runtime_home / "skill-bundle.json")
    current = store.load()
    if current == expected:
        return current
    store.save(expected)
    return expected
