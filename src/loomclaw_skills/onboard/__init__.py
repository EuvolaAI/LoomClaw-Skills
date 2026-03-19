from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loomclaw_skills.onboard.flow import run_onboard

__all__ = ["run_onboard"]


def __getattr__(name: str):
    if name == "run_onboard":
        from loomclaw_skills.onboard.flow import run_onboard

        return run_onboard
    raise AttributeError(name)
