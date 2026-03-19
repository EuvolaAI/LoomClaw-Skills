from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loomclaw_skills.human_bridge.flow import (
        BridgeContext,
        BridgeResult,
        InvitationResponseResult,
        respond_to_bridge_invitation,
        run_bridge_recommendation,
        run_human_bridge,
        sync_bridge_invitation_inbox,
    )

__all__ = [
    "BridgeContext",
    "BridgeResult",
    "InvitationResponseResult",
    "respond_to_bridge_invitation",
    "run_bridge_recommendation",
    "run_human_bridge",
    "sync_bridge_invitation_inbox",
]


def __getattr__(name: str):
    if name in {
        "BridgeContext",
        "BridgeResult",
        "InvitationResponseResult",
        "respond_to_bridge_invitation",
        "run_bridge_recommendation",
        "run_human_bridge",
        "sync_bridge_invitation_inbox",
    }:
        from loomclaw_skills.human_bridge.flow import (
            BridgeContext,
            BridgeResult,
            InvitationResponseResult,
            respond_to_bridge_invitation,
            run_bridge_recommendation,
            run_human_bridge,
            sync_bridge_invitation_inbox,
        )

        return {
            "BridgeContext": BridgeContext,
            "BridgeResult": BridgeResult,
            "InvitationResponseResult": InvitationResponseResult,
            "respond_to_bridge_invitation": respond_to_bridge_invitation,
            "run_bridge_recommendation": run_bridge_recommendation,
            "run_human_bridge": run_human_bridge,
            "sync_bridge_invitation_inbox": sync_bridge_invitation_inbox,
        }[name]
    raise AttributeError(name)
