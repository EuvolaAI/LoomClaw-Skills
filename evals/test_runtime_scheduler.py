from __future__ import annotations

import json
from pathlib import Path


def test_install_local_scheduler_writes_launchd_bundle_and_manifest(tmp_path: Path, monkeypatch) -> None:
    from loomclaw_skills.shared.runtime.scheduler import install_local_scheduler

    runtime_home = tmp_path / "runtime-home"
    launch_agents_dir = tmp_path / "LaunchAgents"
    commands: list[list[str]] = []

    monkeypatch.setenv("LOOMCLAW_LAUNCH_AGENTS_DIR", str(launch_agents_dir))
    monkeypatch.setattr(
        "loomclaw_skills.shared.runtime.scheduler.run_launchctl_command",
        lambda args: commands.append(args),
    )

    result = install_local_scheduler(
        runtime_home,
        base_url="http://loomclaw.ai",
        python_executable="/tmp/loomclaw-python",
        platform_name="darwin",
    )

    manifest = runtime_home / "launchd" / "manifest.json"
    manifest_payload = json.loads(manifest.read_text())

    assert result.platform == "darwin"
    assert manifest.exists()
    assert manifest_payload["launch_agents_dir"] == str(launch_agents_dir)
    assert {job["kind"] for job in manifest_payload["jobs"]} == {
        "social_loop",
        "owner_report",
        "bridge_loop",
    }
    assert len(list((runtime_home / "launchd").glob("*.plist"))) == 3
    assert len(list(launch_agents_dir.glob("*.plist"))) == 3
    assert any(command[:2] == ["launchctl", "bootstrap"] for command in commands)


def test_install_local_scheduler_is_idempotent_for_same_runtime(tmp_path: Path, monkeypatch) -> None:
    from loomclaw_skills.shared.runtime.scheduler import install_local_scheduler

    runtime_home = tmp_path / "runtime-home"
    launch_agents_dir = tmp_path / "LaunchAgents"
    commands: list[list[str]] = []

    monkeypatch.setenv("LOOMCLAW_LAUNCH_AGENTS_DIR", str(launch_agents_dir))
    monkeypatch.setattr(
        "loomclaw_skills.shared.runtime.scheduler.run_launchctl_command",
        lambda args: commands.append(args),
    )

    first = install_local_scheduler(
        runtime_home,
        base_url="http://loomclaw.ai",
        python_executable="/tmp/loomclaw-python",
        platform_name="darwin",
    )
    second = install_local_scheduler(
        runtime_home,
        base_url="http://loomclaw.ai",
        python_executable="/tmp/loomclaw-python",
        platform_name="darwin",
    )

    assert [job.label for job in first.jobs] == [job.label for job in second.jobs]
    assert len(list(launch_agents_dir.glob("*.plist"))) == 3
    assert len(commands) == 12
