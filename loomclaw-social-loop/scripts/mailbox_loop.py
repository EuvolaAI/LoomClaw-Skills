from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.onboard.client import LoomClawClient
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.social_loop.private_social import poll_mailbox


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-home", required=True)
    parser.add_argument("--access-token", required=True)
    args = parser.parse_args()

    runtime_home = Path(args.runtime_home)
    store = RuntimeStateStore(runtime_home / "runtime-state.json")
    state = store.load()
    if state is None:
        raise RuntimeError("runtime-state.json is missing")

    client = LoomClawClient(base_url=args.base_url, access_token=args.access_token)
    poll_mailbox(client, state, runtime_home)
    store.save(state)


if __name__ == "__main__":
    main()
