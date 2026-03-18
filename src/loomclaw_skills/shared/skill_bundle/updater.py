from __future__ import annotations

import hashlib
import io
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path

from loomclaw_skills.shared.schemas.bundle_update import (
    BundleUpdateState,
    ManifestDownloadCandidate,
    SkillsManifest,
)
from loomclaw_skills.shared.skill_bundle.manifest_client import resolve_manifest_url
from loomclaw_skills.shared.skill_bundle.update_state import (
    BundleUpdateStateStore,
    build_default_bundle_update_state,
    future_iso,
    utc_now_iso,
)


@dataclass(frozen=True, slots=True)
class BundleUpdateResult:
    status: str
    version: str | None = None
    reason: str | None = None


class BundleUpdater:
    def __init__(self, manager_root: Path):
        self.manager_root = manager_root
        self.releases_root = manager_root / "releases"
        self.current_symlink = manager_root / "current"
        self.downloads_root = manager_root / "downloads"
        self.state_store = BundleUpdateStateStore(manager_root / "bundle-state.json")

    def load_state(self, *, channel: str = "stable") -> BundleUpdateState:
        state = self.state_store.load()
        if state is not None:
            return state
        state = build_default_bundle_update_state(manager_root=self.manager_root, channel=channel)
        self.state_store.save(state)
        return state

    def apply_manifest(
        self,
        manifest: SkillsManifest,
        *,
        download_bytes,
    ) -> BundleUpdateResult:
        state = self.load_state(channel=manifest.channel)
        now = utc_now_iso()
        state.last_checked_at = now
        state.manifest_url = resolve_manifest_url(manifest.channel)

        if state.current_version == manifest.version:
            state.last_update_status = "noop"
            state.next_check_after = future_iso(hours=24 if manifest.channel == "stable" else 12)
            self.state_store.save(state)
            return BundleUpdateResult(status="noop", version=manifest.version)

        try:
            candidate = self._pick_candidate(manifest)
            payload = download_bytes(candidate)
            self._verify_sha(candidate, payload)
            release_path = self._install_release(manifest.version, payload)

            previous_version = state.current_version if state.current_release_path else None
            self._activate_release(release_path)

            state.rollback_version = previous_version
            state.current_version = manifest.version
            state.current_release_path = f"releases/{manifest.version}"
            state.last_updated_at = now
            state.last_update_status = "updated"
            state.last_failure_at = None
            state.last_failure_reason = None
            state.next_check_after = future_iso(hours=24 if manifest.channel == "stable" else 12)
            self.state_store.save(state)
            return BundleUpdateResult(status="updated", version=manifest.version)
        except Exception as exc:
            state.last_update_status = "failed"
            state.last_failure_at = now
            state.last_failure_reason = str(exc)
            state.next_check_after = future_iso(hours=6)
            self.state_store.save(state)
            return BundleUpdateResult(status="failed", version=state.current_version, reason=str(exc))

    def _pick_candidate(self, manifest: SkillsManifest) -> ManifestDownloadCandidate:
        if not manifest.download_candidates:
            raise ValueError("manifest does not include download candidates")
        return manifest.download_candidates[0]

    def _verify_sha(self, candidate: ManifestDownloadCandidate, payload: bytes) -> None:
        digest = hashlib.sha256(payload).hexdigest()
        if digest != candidate.sha256:
            raise ValueError("download sha256 mismatch")

    def _install_release(self, version: str, payload: bytes) -> Path:
        self.releases_root.mkdir(parents=True, exist_ok=True)
        self.downloads_root.mkdir(parents=True, exist_ok=True)
        extract_root = self.releases_root / version
        temp_root = self.releases_root / f".{version}.tmp"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            try:
                archive.extractall(temp_root, filter="data")
            except TypeError:
                archive.extractall(temp_root)

        nested_root = next((child for child in temp_root.iterdir() if child.is_dir()), None)
        source_root = nested_root or temp_root
        if extract_root.exists():
            shutil.rmtree(extract_root)
        shutil.move(str(source_root), str(extract_root))
        if temp_root.exists():
            shutil.rmtree(temp_root)
        return extract_root

    def _activate_release(self, release_path: Path) -> None:
        self.manager_root.mkdir(parents=True, exist_ok=True)
        tmp_link = self.manager_root / ".current.next"
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()
        tmp_link.symlink_to(release_path, target_is_directory=True)
        tmp_link.replace(self.current_symlink)
