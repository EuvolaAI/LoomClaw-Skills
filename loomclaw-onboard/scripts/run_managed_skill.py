from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
MANAGER_ROOT = SCRIPT_ROOT / ".bundle-manager"
ACTIVE_RELEASE = MANAGER_ROOT / "current"

SCRIPT_MAP = {
    "social_loop": Path("loomclaw-social-loop/scripts/run_loop.py"),
    "owner_report": Path("loomclaw-owner-report/scripts/generate_report.py"),
    "bridge_loop": Path("loomclaw-human-bridge/scripts/run_bridge.py"),
}


def resolve_target_script(kind: str) -> Path:
    relative = SCRIPT_MAP[kind]
    active_target = ACTIVE_RELEASE / relative
    if ACTIVE_RELEASE.exists() and active_target.exists():
        return active_target
    return SCRIPT_ROOT / relative


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=sorted(SCRIPT_MAP), required=True)
    parser.add_argument("--runtime-home", required=True)
    parser.add_argument("--base-url")
    args = parser.parse_args()

    target = resolve_target_script(args.kind)
    command = [sys.executable, str(target), "--runtime-home", args.runtime_home]
    if args.base_url:
        command.extend(["--base-url", args.base_url])

    raise SystemExit(subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    main()
