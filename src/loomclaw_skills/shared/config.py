from __future__ import annotations

import os


DEFAULT_LOOMCLAW_BASE_URL = "http://13.229.227.15:8000"


def resolve_loomclaw_base_url(explicit_base_url: str | None = None) -> str:
    for candidate in (
        explicit_base_url,
        os.getenv("LOOMCLAW_BASE_URL"),
        os.getenv("LOOMCLAW_GATEWAY_URL"),
    ):
        if candidate and candidate.strip():
            return candidate.strip().rstrip("/")

    return DEFAULT_LOOMCLAW_BASE_URL
