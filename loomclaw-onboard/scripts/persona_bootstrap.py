from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.onboard.flow import prepare_persona_runtime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-home", required=True)
    args = parser.parse_args()
    result = prepare_persona_runtime(Path(args.runtime_home))
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
