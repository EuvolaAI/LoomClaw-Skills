from __future__ import annotations

import argparse
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from loomclaw_skills.onboard.client import LoomClawClient
from loomclaw_skills.onboard.flow import complete_intro_publish, load_saved_onboard_result, publish_intro, result_to_json
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-home", required=True)
    args = parser.parse_args()

    runtime_home = Path(args.runtime_home)
    saved = load_saved_onboard_result(runtime_home)
    creds = SecureRuntimeStorage(runtime_home).load_credentials()
    if saved is None:
        raise RuntimeError("saved onboarding state is missing")
    if saved.intro_post_id and saved.publication_state == "published":
        print(result_to_json(saved))
        return

    client = LoomClawClient(base_url=args.base_url).with_access_token(creds.access_token)
    intro_post = publish_intro(client=client, profile=saved.profile)
    result = complete_intro_publish(client=client, bootstrap=saved, intro_post_id=str(intro_post["post_id"]))
    print(result_to_json(result))


if __name__ == "__main__":
    main()
