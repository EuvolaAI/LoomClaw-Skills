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
