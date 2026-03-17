from __future__ import annotations

from loomclaw_skills.shared.config import DEFAULT_LOOMCLAW_BASE_URL, resolve_loomclaw_base_url


def test_default_loomclaw_base_url_is_https() -> None:
    assert DEFAULT_LOOMCLAW_BASE_URL == "https://loomclaw.ai"


def test_resolve_loomclaw_base_url_defaults_to_https() -> None:
    assert resolve_loomclaw_base_url() == "https://loomclaw.ai"
