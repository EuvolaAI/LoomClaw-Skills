from __future__ import annotations

from pathlib import Path

from loomclaw_skills.onboard.client import LoomClawClient
from loomclaw_skills.social_loop.flow import append_activity
from loomclaw_skills.social_loop.private_social import decide_friend_request, handle_friend_request, poll_friend_requests
from loomclaw_skills.social_loop.script_runtime import locked_runtime_state


def process_friend_requests(
    *,
    runtime_home: Path,
    base_url: str,
    access_token: str,
    session=None,
) -> None:
    with locked_runtime_state(runtime_home) as state:
        client = LoomClawClient(base_url=base_url, access_token=access_token, session=session)
        for request in poll_friend_requests(client):
            decision = decide_friend_request(request)
            handle_friend_request(client, state, request, decision=decision)
            append_activity(
                runtime_home / "activity-log.md",
                f"{decision}ed friend request from {request['from_agent_id']}",
            )
