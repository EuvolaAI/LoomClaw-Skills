from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loomclaw_skills.social_loop.flow import SocialLoopResult, run_social_loop

__all__ = ["SocialLoopResult", "run_social_loop"]


def __getattr__(name: str):
    if name in {"SocialLoopResult", "run_social_loop"}:
        from loomclaw_skills.social_loop.flow import SocialLoopResult, run_social_loop

        return {
            "SocialLoopResult": SocialLoopResult,
            "run_social_loop": run_social_loop,
        }[name]
    raise AttributeError(name)
