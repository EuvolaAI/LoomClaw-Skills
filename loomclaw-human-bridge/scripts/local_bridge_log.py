from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.human_bridge.local_log import append_bridge_log


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--entry-id", required=True)
    parser.add_argument("--peer-agent-id", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--created-at", required=True)
    parser.add_argument("--consent-source", required=True)
    parser.add_argument("--status", required=True)
    args = parser.parse_args()
    append_bridge_log(
        Path(args.path),
        title=args.title,
        entry_id=args.entry_id,
        peer_agent_id=args.peer_agent_id,
        summary_markdown=args.summary,
        created_at=args.created_at,
        consent_source=args.consent_source,
        status=args.status,
    )


if __name__ == "__main__":
    main()
