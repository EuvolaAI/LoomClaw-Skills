from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import httpx


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.shared.schemas.bundle_update import SkillsManifest
from loomclaw_skills.shared.skill_bundle.manifest_client import resolve_manifest_url
from loomclaw_skills.shared.skill_bundle.updater import BundleUpdater


def default_manager_root() -> Path:
    return SCRIPT_ROOT / ".bundle-manager"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=["stable", "beta"], default="stable")
    parser.add_argument("--manifest-url")
    parser.add_argument("--manager-root")
    args = parser.parse_args()

    manager_root = Path(args.manager_root).expanduser() if args.manager_root else default_manager_root()
    manifest_url = args.manifest_url or resolve_manifest_url(args.channel)

    with httpx.Client(timeout=20.0) as client:
        manifest_response = client.get(manifest_url)
        manifest_response.raise_for_status()
        manifest = SkillsManifest.model_validate(manifest_response.json())

        updater = BundleUpdater(manager_root)
        result = updater.apply_manifest(
            manifest,
            download_bytes=lambda candidate: fetch_release_bytes(client, candidate.url),
        )

    state = updater.state_store.load()
    print(
        json.dumps(
            {
                "manifest_url": manifest_url,
                "manager_root": str(manager_root),
                "result": {
                    "status": result.status,
                    "version": result.version,
                    "reason": result.reason,
                },
                "state": state.model_dump() if state else None,
            },
            indent=2,
        )
    )


def fetch_release_bytes(client: httpx.Client, url: str) -> bytes:
    response = client.get(url)
    response.raise_for_status()
    return response.content


if __name__ == "__main__":
    main()
