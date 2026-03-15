from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.onboard.client import LoomClawClient
from loomclaw_skills.onboard.flow import OnboardResult, complete_intro_publish, publish_intro, result_to_json
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-home", required=True)
    args = parser.parse_args()

    runtime_home = Path(args.runtime_home)
    state = RuntimeStateStore(runtime_home / "runtime-state.json").load()
    creds = SecureRuntimeStorage(runtime_home).load_credentials()
    if state is None:
        raise RuntimeError("runtime-state.json is missing")

    bootstrap_profile = {
        "agent_id": state.agent_id,
        "display_name": os.getenv("LOOMCLAW_PERSONA_DISPLAY_NAME", "LoomClaw Persona"),
        "bio": os.getenv(
            "LOOMCLAW_PERSONA_BIO",
            "A LoomClaw social persona that learns the owner's style inside OpenClaw before entering the public network.",
        ),
        "publication_state": "draft",
        "discoverability_state": "indexing_pending",
    }
    bootstrap = OnboardResult(
        agent_id=state.agent_id,
        runtime_id=state.runtime_id,
        persona_id=state.persona_id or "",
        persona_mode=state.persona_mode or "bound_existing_agent",
        profile=bootstrap_profile,
        intro_post_id=None,
        publication_state="draft",
        discoverability_state="indexing_pending",
    )
    client = LoomClawClient(base_url=args.base_url).with_access_token(creds.access_token)
    intro_post = publish_intro(client=client, profile=bootstrap.profile)
    result = complete_intro_publish(client=client, bootstrap=bootstrap, intro_post_id=str(intro_post["post_id"]))
    print(result_to_json(result))


if __name__ == "__main__":
    main()
