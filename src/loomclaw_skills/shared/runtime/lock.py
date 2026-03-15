from threading import Lock


class RuntimeLock:
    _registry_guard = Lock()
    _locked_agent_ids: set[str] = set()

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._held = False

    def acquire(self) -> bool:
        with self._registry_guard:
            if self._held:
                return False

            if self.agent_id in self._locked_agent_ids:
                return False

            self._locked_agent_ids.add(self.agent_id)
            self._held = True
            return True

    def release(self) -> None:
        with self._registry_guard:
            if not self._held:
                return

            self._locked_agent_ids.discard(self.agent_id)
            self._held = False
