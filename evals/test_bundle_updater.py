from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path


def build_release_tarball_bytes(*, version: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = f"version={version}\n".encode()
        info = tarfile.TarInfo(name=f"loomclaw-skills-{version}/VERSION")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_manifest_client_uses_official_defaults() -> None:
    from loomclaw_skills.shared.skill_bundle.manifest_client import resolve_manifest_url

    assert resolve_manifest_url("stable") == "https://loomclaw.ai/skills/manifest/stable.json"
    assert resolve_manifest_url("beta") == "https://loomclaw.ai/skills/manifest/beta.json"


def test_manifest_client_allows_env_override(monkeypatch) -> None:
    from loomclaw_skills.shared.skill_bundle.manifest_client import resolve_manifest_url

    monkeypatch.setenv("LOOMCLAW_SKILLS_MANIFEST_URL", "https://mirror.example/manifest.json")

    assert resolve_manifest_url("stable") == "https://mirror.example/manifest.json"


def test_bundle_updater_noops_when_manifest_matches_current_version(tmp_path: Path) -> None:
    from loomclaw_skills.shared.schemas.bundle_update import BundleUpdateState, SkillsManifest
    from loomclaw_skills.shared.skill_bundle.update_state import BundleUpdateStateStore
    from loomclaw_skills.shared.skill_bundle.updater import BundleUpdater

    manager_root = tmp_path / "bundle"
    manager_root.mkdir()
    store = BundleUpdateStateStore(manager_root / "bundle-state.json")
    store.save(
        BundleUpdateState(
            product="loomclaw-skills",
            channel="stable",
            current_version="0.5.0",
            current_release_path="releases/0.5.0",
            manifest_url="https://loomclaw.ai/skills/manifest/stable.json",
            next_check_after="2026-03-19T12:00:00Z",
        )
    )
    updater = BundleUpdater(manager_root)
    manifest = SkillsManifest.model_validate(
        {
            "product": "loomclaw-skills",
            "channel": "stable",
            "version": "0.5.0",
            "published_at": "2026-03-18T08:00:00Z",
            "bundle_schema_version": 1,
            "minimum_backend_version": "0.5.0",
            "download_candidates": [],
        }
    )

    result = updater.apply_manifest(manifest, download_bytes=lambda _candidate: b"")

    assert result.status == "noop"


def test_bundle_updater_downloads_unpack_switches_and_records_state(tmp_path: Path) -> None:
    from loomclaw_skills.shared.schemas.bundle_update import SkillsManifest
    from loomclaw_skills.shared.skill_bundle.updater import BundleUpdater

    manager_root = tmp_path / "bundle"
    manager_root.mkdir()
    tarball = build_release_tarball_bytes(version="0.5.1")
    sha = hashlib.sha256(tarball).hexdigest()
    updater = BundleUpdater(manager_root)
    manifest = SkillsManifest.model_validate(
        {
            "product": "loomclaw-skills",
            "channel": "stable",
            "version": "0.5.1",
            "published_at": "2026-03-18T08:00:00Z",
            "bundle_schema_version": 1,
            "minimum_backend_version": "0.5.0",
            "download_candidates": [
                {
                    "provider": "loomclaw",
                    "type": "tarball",
                    "url": "https://loomclaw.ai/skills/releases/0.5.1/loomclaw-skills-0.5.1.tar.gz",
                    "sha256": sha,
                }
            ],
        }
    )

    result = updater.apply_manifest(manifest, download_bytes=lambda _candidate: tarball)

    assert result.status == "updated"
    assert (manager_root / "current").is_symlink()
    assert (manager_root / "releases" / "0.5.1" / "VERSION").exists()
    assert "0.5.1" in (manager_root / "current").resolve().as_posix()


def test_bundle_updater_keeps_previous_release_for_rollback(tmp_path: Path) -> None:
    from loomclaw_skills.shared.schemas.bundle_update import SkillsManifest
    from loomclaw_skills.shared.skill_bundle.updater import BundleUpdater

    manager_root = tmp_path / "bundle"
    manager_root.mkdir()
    updater = BundleUpdater(manager_root)

    first = build_release_tarball_bytes(version="0.5.0")
    second = build_release_tarball_bytes(version="0.5.1")

    first_manifest = SkillsManifest.model_validate(
        {
            "product": "loomclaw-skills",
            "channel": "stable",
            "version": "0.5.0",
            "published_at": "2026-03-18T08:00:00Z",
            "bundle_schema_version": 1,
            "minimum_backend_version": "0.5.0",
            "download_candidates": [
                {
                    "provider": "loomclaw",
                    "type": "tarball",
                    "url": "https://loomclaw.ai/skills/releases/0.5.0/loomclaw-skills-0.5.0.tar.gz",
                    "sha256": hashlib.sha256(first).hexdigest(),
                }
            ],
        }
    )
    second_manifest = SkillsManifest.model_validate(
        {
            "product": "loomclaw-skills",
            "channel": "stable",
            "version": "0.5.1",
            "published_at": "2026-03-19T08:00:00Z",
            "bundle_schema_version": 1,
            "minimum_backend_version": "0.5.0",
            "download_candidates": [
                {
                    "provider": "loomclaw",
                    "type": "tarball",
                    "url": "https://loomclaw.ai/skills/releases/0.5.1/loomclaw-skills-0.5.1.tar.gz",
                    "sha256": hashlib.sha256(second).hexdigest(),
                }
            ],
        }
    )

    updater.apply_manifest(first_manifest, download_bytes=lambda _candidate: first)
    updater.apply_manifest(second_manifest, download_bytes=lambda _candidate: second)

    state = updater.state_store.load()
    assert state is not None
    assert state.current_version == "0.5.1"
    assert state.rollback_version == "0.5.0"


def test_bundle_update_script_records_failure_without_clobbering_current(tmp_path: Path) -> None:
    from loomclaw_skills.shared.schemas.bundle_update import SkillsManifest
    from loomclaw_skills.shared.skill_bundle.updater import BundleUpdater

    manager_root = tmp_path / "bundle"
    manager_root.mkdir()
    updater = BundleUpdater(manager_root)
    tarball = build_release_tarball_bytes(version="0.5.0")
    good_manifest = SkillsManifest.model_validate(
        {
            "product": "loomclaw-skills",
            "channel": "stable",
            "version": "0.5.0",
            "published_at": "2026-03-18T08:00:00Z",
            "bundle_schema_version": 1,
            "minimum_backend_version": "0.5.0",
            "download_candidates": [
                {
                    "provider": "loomclaw",
                    "type": "tarball",
                    "url": "https://loomclaw.ai/skills/releases/0.5.0/loomclaw-skills-0.5.0.tar.gz",
                    "sha256": hashlib.sha256(tarball).hexdigest(),
                }
            ],
        }
    )
    updater.apply_manifest(good_manifest, download_bytes=lambda _candidate: tarball)

    bad_manifest = SkillsManifest.model_validate(
        {
            "product": "loomclaw-skills",
            "channel": "stable",
            "version": "0.5.1",
            "published_at": "2026-03-19T08:00:00Z",
            "bundle_schema_version": 1,
            "minimum_backend_version": "0.5.0",
            "download_candidates": [
                {
                    "provider": "loomclaw",
                    "type": "tarball",
                    "url": "https://loomclaw.ai/skills/releases/0.5.1/loomclaw-skills-0.5.1.tar.gz",
                    "sha256": "not-the-right-hash",
                }
            ],
        }
    )

    result = updater.apply_manifest(bad_manifest, download_bytes=lambda _candidate: build_release_tarball_bytes(version="0.5.1"))

    state = updater.state_store.load()
    assert result.status == "failed"
    assert state is not None
    assert state.current_version == "0.5.0"
    assert state.last_update_status == "failed"


def test_initialize_bundle_manager_uses_env_channel(monkeypatch, tmp_path: Path) -> None:
    from loomclaw_skills.shared.skill_bundle.updater import initialize_bundle_manager

    monkeypatch.setenv("LOOMCLAW_SKILLS_UPDATE_CHANNEL", "beta")
    state = initialize_bundle_manager(manager_root=tmp_path / "bundle", source_root=tmp_path)

    assert state.channel == "beta"
    assert state.manifest_url == "https://loomclaw.ai/skills/manifest/beta.json"
