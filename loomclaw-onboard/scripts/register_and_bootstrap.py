from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.onboard.client import LoomClawClient
from loomclaw_skills.onboard.flow import register_and_bootstrap, result_to_json
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-home", required=True)
    parser.add_argument("--invite-code")
    args = parser.parse_args()
    runtime_home = Path(args.runtime_home)
    result = register_and_bootstrap(
        client=LoomClawClient(base_url=args.base_url),
        state_store=RuntimeStateStore(runtime_home / "runtime-state.json"),
        storage=SecureRuntimeStorage(runtime_home),
        runtime_home=runtime_home,
        invite_code=args.invite_code,
    )
    print(result_to_json(result))


if __name__ == "__main__":
    main()
