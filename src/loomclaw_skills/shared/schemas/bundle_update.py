from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ManifestReleaseNotes(BaseModel):
    title: str | None = None
    summary: str | None = None


class ManifestDownloadCandidate(BaseModel):
    provider: str
    type: Literal["tarball", "package"] = "tarball"
    url: str
    sha256: str


class ManifestSignature(BaseModel):
    algorithm: str
    value: str


class SkillsManifest(BaseModel):
    product: str
    channel: Literal["stable", "beta"]
    version: str
    published_at: str
    bundle_schema_version: int = 1
    minimum_backend_version: str | None = None
    minimum_bundle_schema_version: int | None = None
    release_notes: ManifestReleaseNotes | None = None
    download_candidates: list[ManifestDownloadCandidate] = Field(default_factory=list)
    signature: ManifestSignature | None = None


class BundleUpdateState(BaseModel):
    product: str = "loomclaw-skills"
    channel: Literal["stable", "beta"] = "stable"
    current_version: str = "0.0.0"
    current_release_path: str | None = None
    manifest_url: str
    last_checked_at: str | None = None
    last_updated_at: str | None = None
    next_check_after: str | None = None
    rollback_version: str | None = None
    last_update_status: Literal["idle", "noop", "updated", "failed"] = "idle"
    last_failure_at: str | None = None
    last_failure_reason: str | None = None
