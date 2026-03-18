from __future__ import annotations

import os


DEFAULT_MANIFEST_URLS = {
    "stable": "https://loomclaw.ai/skills/manifest/stable.json",
    "beta": "https://loomclaw.ai/skills/manifest/beta.json",
}


def resolve_manifest_url(channel: str) -> str:
    override = os.getenv("LOOMCLAW_SKILLS_MANIFEST_URL")
    if override:
        return override
    return DEFAULT_MANIFEST_URLS[channel]
