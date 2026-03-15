from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.human_bridge.flow import run_human_bridge


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-home", required=True)
    args = parser.parse_args()
    result = run_human_bridge(args.base_url, Path(args.runtime_home))
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
