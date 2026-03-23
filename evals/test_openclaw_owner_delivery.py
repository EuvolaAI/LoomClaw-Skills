from __future__ import annotations

import json
from pathlib import Path


def test_install_owner_report_delivery_registers_openclaw_cron_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loomclaw_skills.shared.runtime.openclaw_delivery import install_owner_report_delivery

    runtime_home = tmp_path / "runtime-home"
    commands: list[list[str]] = []

    def fake_run(args: list[str], *, check: bool, capture_output: bool, text: bool):  # type: ignore[no-untyped-def]
        commands.append(list(args))
        if args[1:5] == ["cron", "list", "--all", "--json"]:
            return type("Completed", (), {"stdout": json.dumps({"jobs": []})})()
        if args[1:3] == ["cron", "add"]:
            return type("Completed", (), {"stdout": json.dumps({"id": "job-owner-report-1"})})()
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(
        "loomclaw_skills.shared.runtime.openclaw_delivery.resolve_openclaw_cli",
        lambda: ["openclaw"],
    )
    monkeypatch.setattr("loomclaw_skills.shared.runtime.openclaw_delivery.subprocess.run", fake_run)

    result = install_owner_report_delivery(runtime_home)

    assert result.status == "registered"
    assert result.backend == "openclaw_cron"
    assert result.job_id == "job-owner-report-1"
    assert result.manifest_path.exists()
    add_command = next(command for command in commands if command[1:3] == ["cron", "add"])
    assert "--announce" in add_command
    assert "--channel" in add_command
    assert "last" in add_command
    assert "--cron" in add_command
    assert "0 20 * * *" in add_command


def test_install_owner_report_delivery_updates_existing_job_instead_of_duplicating(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from loomclaw_skills.shared.runtime.openclaw_delivery import install_owner_report_delivery

    runtime_home = tmp_path / "runtime-home"
    commands: list[list[str]] = []

    def fake_run(args: list[str], *, check: bool, capture_output: bool, text: bool):  # type: ignore[no-untyped-def]
        commands.append(list(args))
        if args[1:5] == ["cron", "list", "--all", "--json"]:
            return type(
                "Completed",
                (),
                {
                    "stdout": json.dumps(
                        {
                            "jobs": [
                                {
                                    "id": "job-owner-report-1",
                                    "name": f"LoomClaw owner report / {runtime_home.name}",
                                }
                            ]
                        }
                    )
                },
            )()
        if args[1:3] == ["cron", "edit"]:
            return type("Completed", (), {"stdout": json.dumps({"id": "job-owner-report-1"})})()
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(
        "loomclaw_skills.shared.runtime.openclaw_delivery.resolve_openclaw_cli",
        lambda: ["openclaw"],
    )
    monkeypatch.setattr("loomclaw_skills.shared.runtime.openclaw_delivery.subprocess.run", fake_run)

    result = install_owner_report_delivery(runtime_home)

    assert result.status == "updated"
    assert result.job_id == "job-owner-report-1"
    assert not any(command[1:3] == ["cron", "add"] for command in commands)
    assert any(command[1:3] == ["cron", "edit"] for command in commands)


def test_install_owner_report_delivery_records_unavailable_cli_without_failing(tmp_path: Path, monkeypatch) -> None:
    from loomclaw_skills.shared.runtime.openclaw_delivery import install_owner_report_delivery

    runtime_home = tmp_path / "runtime-home"

    monkeypatch.setattr(
        "loomclaw_skills.shared.runtime.openclaw_delivery.resolve_openclaw_cli",
        lambda: None,
    )

    result = install_owner_report_delivery(runtime_home)
    manifest = json.loads(result.manifest_path.read_text())

    assert result.status == "unavailable"
    assert result.job_id is None
    assert manifest["status"] == "unavailable"
    assert manifest["backend"] == "openclaw_cron"


def test_ensure_owner_report_delivery_installs_for_legacy_runtime_without_manifest(tmp_path: Path, monkeypatch) -> None:
    from loomclaw_skills.shared.runtime.openclaw_delivery import ensure_owner_report_delivery

    runtime_home = tmp_path / "runtime-home"
    calls: list[Path] = []

    def fake_install(target: Path):  # type: ignore[no-untyped-def]
        calls.append(target)
        manifest_path = target / "openclaw" / "owner-report-delivery.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps({"status": "registered", "backend": "openclaw_cron"}))
        return None

    monkeypatch.setattr(
        "loomclaw_skills.shared.runtime.openclaw_delivery.install_owner_report_delivery",
        fake_install,
    )

    ensure_owner_report_delivery(runtime_home)

    assert calls == [runtime_home]


def test_ensure_owner_report_delivery_skips_when_manifest_is_ready(tmp_path: Path, monkeypatch) -> None:
    from loomclaw_skills.shared.runtime.openclaw_delivery import ensure_owner_report_delivery

    runtime_home = tmp_path / "runtime-home"
    manifest_path = runtime_home / "openclaw" / "owner-report-delivery.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"status": "updated", "backend": "openclaw_cron"}))
    invoked = False

    def fake_install(target: Path):  # type: ignore[no-untyped-def]
        nonlocal invoked
        invoked = True
        raise AssertionError(f"unexpected install call for {target}")

    monkeypatch.setattr(
        "loomclaw_skills.shared.runtime.openclaw_delivery.install_owner_report_delivery",
        fake_install,
    )

    ensure_owner_report_delivery(runtime_home)

    assert invoked is False
