from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def test_bundle_update_state_round_trips_json(tmp_path: Path) -> None:
    from loomclaw_skills.shared.schemas.bundle_update import BundleUpdateState
    from loomclaw_skills.shared.skill_bundle.update_state import BundleUpdateStateStore

    state = BundleUpdateState(
        product="loomclaw-skills",
        channel="stable",
        current_version="0.5.0",
        current_release_path="releases/0.5.0",
        manifest_url="https://loomclaw.ai/skills/manifest/stable.json",
        last_checked_at="2026-03-18T12:00:00Z",
        next_check_after="2026-03-19T12:00:00Z",
        rollback_version="0.4.9",
    )
    store = BundleUpdateStateStore(tmp_path / "bundle-state.json")

    store.save(state)

    loaded = store.load()
    assert loaded == state


def test_build_default_bundle_update_state_uses_official_stable_manifest(tmp_path: Path) -> None:
    from loomclaw_skills.shared.skill_bundle.update_state import build_default_bundle_update_state

    state = build_default_bundle_update_state(manager_root=tmp_path)

    assert state.product == "loomclaw-skills"
    assert state.channel == "stable"
    assert state.manifest_url == "https://loomclaw.ai/skills/manifest/stable.json"
    assert state.current_version == "0.0.0"
    assert state.current_release_path is None
    assert state.next_check_after is not None


def test_manifest_model_parses_download_candidates() -> None:
    from loomclaw_skills.shared.schemas.bundle_update import SkillsManifest

    manifest = SkillsManifest.model_validate(
        {
            "product": "loomclaw-skills",
            "channel": "stable",
            "version": "0.5.0",
            "published_at": "2026-03-18T08:00:00Z",
            "bundle_schema_version": 1,
            "minimum_backend_version": "0.5.0",
            "release_notes": {
                "title": "Stability improvements",
                "summary": "Reduced loop pressure.",
            },
            "download_candidates": [
                {
                    "provider": "loomclaw",
                    "type": "tarball",
                    "url": "https://loomclaw.ai/skills/releases/0.5.0/loomclaw-skills-0.5.0.tar.gz",
                    "sha256": "abc123",
                }
            ],
            "signature": {
                "algorithm": "ed25519",
                "value": "deadbeef",
            },
        }
    )

    assert manifest.version == "0.5.0"
    assert manifest.download_candidates[0].provider == "loomclaw"
    assert manifest.signature is not None


def test_state_store_can_record_failure_metadata(tmp_path: Path) -> None:
    from loomclaw_skills.shared.schemas.bundle_update import BundleUpdateState
    from loomclaw_skills.shared.skill_bundle.update_state import BundleUpdateStateStore

    state = BundleUpdateState(
        product="loomclaw-skills",
        channel="stable",
        current_version="0.5.0",
        current_release_path="releases/0.5.0",
        manifest_url="https://loomclaw.ai/skills/manifest/stable.json",
        last_checked_at="2026-03-18T12:00:00Z",
        next_check_after="2026-03-19T12:00:00Z",
        last_update_status="failed",
        last_failure_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        last_failure_reason="download timeout",
    )
    store = BundleUpdateStateStore(tmp_path / "bundle-state.json")

    store.save(state)

    loaded = store.load()
    assert loaded is not None
    assert loaded.last_update_status == "failed"
    assert loaded.last_failure_reason == "download timeout"
