from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from loomclaw_skills.shared.runtime.lock import RuntimeLock
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState


class RuntimeBusyError(RuntimeError):
    pass


@contextmanager
def locked_runtime_state(runtime_home: Path) -> Iterator[RuntimeState]:
    store = RuntimeStateStore(runtime_home / "runtime-state.json")
    state = store.load()
    if state is None:
        raise RuntimeError("runtime-state.json is missing")

    lock = RuntimeLock(state.agent_id)
    if not lock.acquire():
        raise RuntimeBusyError(state.agent_id)
    try:
        yield state
        store.save(state)
    finally:
        lock.release()
