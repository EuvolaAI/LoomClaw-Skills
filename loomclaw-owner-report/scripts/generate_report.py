from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.owner_report.report import generate_owner_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-home", required=True)
    args = parser.parse_args()
    result = generate_owner_report(Path(args.runtime_home))
    print(result.path)


if __name__ == "__main__":
    main()
