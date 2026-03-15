from loomclaw_skills.shared.schemas.runtime_state import RuntimeState
from loomclaw_skills.shared.schemas.activity_log import ActivityLogEntry
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.lock import RuntimeLock
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage


def test_runtime_state_requires_agent_and_runtime_ids():
    state = RuntimeState(
        agent_id="agent-1",
        runtime_id="runtime-1",
        username="loom",
    )

    assert state.agent_id == "agent-1"


def test_runtime_state_store_round_trips_json(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime-state.json")
    state = RuntimeState(
        agent_id="agent-1",
        runtime_id="runtime-1",
        username="loom",
        feed_cursor="cursor-1",
        pending_jobs=["sync-feed"],
        retry_queue=["retry-follow"],
        relationship_cache={"agent-2": "following"},
    )

    store.save(state)
    loaded = store.load()

    assert loaded == state


def test_secure_storage_round_trips_credentials(tmp_path):
    storage = SecureRuntimeStorage(tmp_path)

    storage.save_credentials(
        username="loom",
        password="pw",
        access_token="access-token",
        refresh_token="refresh-token",
    )
    creds = storage.load_credentials()

    assert creds.username == "loom"
    assert creds.password == "pw"
    assert creds.access_token == "access-token"
    assert creds.refresh_token == "refresh-token"


def test_activity_log_entry_records_event_type_and_timestamp():
    entry = ActivityLogEntry(
        event_type="follow_created",
        happened_at="2026-03-15T00:00:00Z",
    )

    assert entry.event_type == "follow_created"
    assert entry.happened_at == "2026-03-15T00:00:00Z"


def test_runtime_lock_is_single_writer():
    lock = RuntimeLock("agent-1")

    assert lock.acquire() is True
    assert lock.acquire() is False

    lock.release()

    assert lock.acquire() is True
    lock.release()


def test_runtime_lock_blocks_second_instance_for_same_agent():
    first_lock = RuntimeLock("agent-1")
    second_lock = RuntimeLock("agent-1")

    assert first_lock.acquire() is True
    assert second_lock.acquire() is False

    first_lock.release()

    assert second_lock.acquire() is True
    second_lock.release()
