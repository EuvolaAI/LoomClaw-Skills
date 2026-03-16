from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.social_loop.flow import run_social_loop
from loomclaw_skills.shared.config import resolve_loomclaw_base_url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url")
    parser.add_argument("--runtime-home", required=True)
    args = parser.parse_args()
    result = run_social_loop(resolve_loomclaw_base_url(args.base_url), Path(args.runtime_home))
    print(result)


if __name__ == "__main__":
    main()
