from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.social_loop.script_actions import process_friend_requests
from loomclaw_skills.shared.config import resolve_loomclaw_base_url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url")
    parser.add_argument("--runtime-home", required=True)
    parser.add_argument("--access-token", required=True)
    args = parser.parse_args()

    process_friend_requests(
        runtime_home=Path(args.runtime_home),
        base_url=resolve_loomclaw_base_url(args.base_url),
        access_token=args.access_token,
    )


if __name__ == "__main__":
    main()
