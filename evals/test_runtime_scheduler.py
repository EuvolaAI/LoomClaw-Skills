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
        base_url="https://loomclaw.ai",
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
        base_url="https://loomclaw.ai",
        python_executable="/tmp/loomclaw-python",
        platform_name="darwin",
    )
    second = install_local_scheduler(
        runtime_home,
        base_url="https://loomclaw.ai",
        python_executable="/tmp/loomclaw-python",
        platform_name="darwin",
    )

    assert [job.label for job in first.jobs] == [job.label for job in second.jobs]
    assert len(list(launch_agents_dir.glob("*.plist"))) == 3
    assert len(commands) == 12


def test_install_local_scheduler_writes_linux_cron_bundle_and_manifest(tmp_path: Path, monkeypatch) -> None:
    from loomclaw_skills.shared.runtime.scheduler import install_local_scheduler

    runtime_home = tmp_path / "runtime-home"
    cron_dir = tmp_path / "cron"
    installed: list[str] = []

    monkeypatch.setenv("LOOMCLAW_CRON_DIR", str(cron_dir))
    monkeypatch.setattr(
        "loomclaw_skills.shared.runtime.scheduler.read_user_crontab",
        lambda: "",
    )
    monkeypatch.setattr(
        "loomclaw_skills.shared.runtime.scheduler.install_user_crontab",
        lambda content: installed.append(content),
    )

    result = install_local_scheduler(
        runtime_home,
        base_url="https://loomclaw.ai",
        python_executable="/tmp/loomclaw-python",
        platform_name="linux",
    )

    manifest = cron_dir / "manifest.json"
    manifest_payload = json.loads(manifest.read_text())
    bundle = (cron_dir / "loomclaw.crontab").read_text()

    assert result.platform == "linux"
    assert result.scheduler_backend == "cron"
    assert manifest.exists()
    assert manifest_payload["scheduler_backend"] == "cron"
    assert manifest_payload["install_root"] == str(cron_dir)
    assert {job["kind"] for job in manifest_payload["jobs"]} == {
        "social_loop",
        "owner_report",
        "bridge_loop",
    }
    assert "LOOMCLAW-BEGIN" in bundle
    assert "*/30 * * * *" in bundle
    assert "0 20 * * *" in bundle
    assert "*/15 * * * *" in bundle
    assert installed == [bundle]


def test_install_local_scheduler_replaces_existing_linux_managed_block(tmp_path: Path, monkeypatch) -> None:
    from loomclaw_skills.shared.runtime.scheduler import install_local_scheduler

    runtime_home = tmp_path / "runtime-home"
    cron_dir = tmp_path / "cron"
    installed: list[str] = []
    existing = "\n".join(
        [
            "MAILTO=\"\"",
            "# LOOMCLAW-BEGIN runtime-home",
            "old line",
            "# LOOMCLAW-END runtime-home",
            "0 8 * * * echo keep-me",
        ]
    )

    monkeypatch.setenv("LOOMCLAW_CRON_DIR", str(cron_dir))
    monkeypatch.setattr(
        "loomclaw_skills.shared.runtime.scheduler.read_user_crontab",
        lambda: existing,
    )
    monkeypatch.setattr(
        "loomclaw_skills.shared.runtime.scheduler.install_user_crontab",
        lambda content: installed.append(content),
    )

    install_local_scheduler(
        runtime_home,
        base_url="https://loomclaw.ai",
        python_executable="/tmp/loomclaw-python",
        platform_name="linux",
    )

    assert installed
    assert "old line" not in installed[0]
    assert "keep-me" in installed[0]
