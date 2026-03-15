from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.social_loop.persona_learning import collect_local_acp_observations, refine_persona


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-home", required=True)
    args = parser.parse_args()
    runtime_home = Path(args.runtime_home)
    observations = collect_local_acp_observations(runtime_home)
    print(refine_persona(runtime_home, observations))


if __name__ == "__main__":
    main()
