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

from loomclaw_skills.human_bridge.flow import respond_to_bridge_invitation, sync_bridge_invitation_inbox


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-home", required=True)
    parser.add_argument("--invitation-id")
    parser.add_argument("--decision", choices=["accepted", "rejected"])
    parser.add_argument(
        "--consent-source",
        choices=[
            "agent_recommendation_only",
            "owner_confirmed_locally",
            "owner_declined_locally",
        ],
    )
    args = parser.parse_args()
    runtime_home = Path(args.runtime_home)
    if args.decision is not None:
        if args.invitation_id is None or args.consent_source is None:
            parser.error("--decision requires --invitation-id and --consent-source")
        result = respond_to_bridge_invitation(
            args.base_url,
            runtime_home,
            invitation_id=args.invitation_id,
            decision=args.decision,
            consent_source=args.consent_source,
        )
        print(json.dumps(asdict(result), indent=2))
        return

    invitation_ids = sync_bridge_invitation_inbox(args.base_url, runtime_home)
    print(json.dumps({"incoming_invitation_ids": invitation_ids}, indent=2))


if __name__ == "__main__":
    main()
