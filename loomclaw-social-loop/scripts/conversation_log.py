from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.social_loop.conversation import append_conversation_markdown


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--direction", required=True)
    parser.add_argument("--sender", required=True)
    parser.add_argument("--content", required=True)
    parser.add_argument("--created-at", required=True)
    args = parser.parse_args()
    append_conversation_markdown(
        Path(args.path),
        direction=args.direction,
        sender=args.sender,
        content=args.content,
        created_at=args.created_at,
    )


if __name__ == "__main__":
    main()
