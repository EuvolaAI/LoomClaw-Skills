from __future__ import annotations

import contextlib
import importlib
import io
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

for script_root in (
    REPO_ROOT / "loomclaw-onboard" / "scripts",
    REPO_ROOT / "loomclaw-social-loop" / "scripts",
):
    if str(script_root) not in sys.path:
        sys.path.insert(0, str(script_root))


def test_resolve_loomclaw_base_url_defaults_to_test_server(monkeypatch) -> None:
    monkeypatch.delenv("LOOMCLAW_BASE_URL", raising=False)
    monkeypatch.delenv("LOOMCLAW_GATEWAY_URL", raising=False)

    from loomclaw_skills.shared.config import DEFAULT_LOOMCLAW_BASE_URL, resolve_loomclaw_base_url

    assert DEFAULT_LOOMCLAW_BASE_URL == "http://13.229.227.15:8000"
    assert resolve_loomclaw_base_url() == "http://13.229.227.15:8000"


def test_resolve_loomclaw_base_url_prefers_env_override(monkeypatch) -> None:
    monkeypatch.setenv("LOOMCLAW_BASE_URL", "http://127.0.0.1:8000")

    from loomclaw_skills.shared.config import resolve_loomclaw_base_url

    assert resolve_loomclaw_base_url() == "http://127.0.0.1:8000"


def test_run_onboard_script_uses_default_base_url_when_flag_missing(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("run_onboard")
    seen: dict[str, object] = {}

    monkeypatch.delenv("LOOMCLAW_BASE_URL", raising=False)
    monkeypatch.setattr(
        module,
        "run_onboard",
        lambda base_url, runtime_home, invite_code=None: seen.update(
            {"base_url": base_url, "runtime_home": runtime_home, "invite_code": invite_code}
        )
        or {"ok": True},
    )
    monkeypatch.setattr(module, "result_to_json", lambda result: "ok")
    monkeypatch.setattr(module, "Path", Path)
    monkeypatch.setattr(sys, "argv", ["run_onboard.py", "--runtime-home", str(tmp_path)])

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module.main()

    assert seen["base_url"] == "http://13.229.227.15:8000"
    assert seen["runtime_home"] == tmp_path


def test_run_social_loop_script_uses_env_base_url_when_flag_missing(monkeypatch, tmp_path: Path) -> None:
    module = importlib.import_module("run_loop")
    seen: dict[str, object] = {}

    monkeypatch.setenv("LOOMCLAW_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(
        module,
        "run_social_loop",
        lambda base_url, runtime_home: seen.update({"base_url": base_url, "runtime_home": runtime_home}) or {"ok": True},
    )
    monkeypatch.setattr(module, "Path", Path)
    monkeypatch.setattr(sys, "argv", ["run_loop.py", "--runtime-home", str(tmp_path)])

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        module.main()

    assert seen["base_url"] == "http://127.0.0.1:8000"
    assert seen["runtime_home"] == tmp_path
