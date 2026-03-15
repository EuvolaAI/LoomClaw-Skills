from pathlib import Path

from loomclaw_skills.shared.schemas.runtime_state import RuntimeState


class RuntimeStateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> RuntimeState | None:
        if not self.path.exists():
            return None

        return RuntimeState.model_validate_json(self.path.read_text())

    def save(self, state: RuntimeState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(state.model_dump_json(indent=2))
