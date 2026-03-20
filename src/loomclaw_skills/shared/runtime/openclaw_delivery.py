from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OWNER_REPORT_CRON_SCHEDULE = "0 20 * * *"
OWNER_REPORT_SCHEDULE_DESCRIPTION = "every day at 20:00 local time"


@dataclass(frozen=True, slots=True)
class OpenClawCronInstallResult:
    backend: str
    status: str
    job_id: str | None
    job_name: str
    manifest_path: Path
    schedule_description: str
    cli_command: list[str]
    reason: str | None = None


def install_owner_report_delivery(runtime_home: Path) -> OpenClawCronInstallResult:
    manifest_path = runtime_home / "openclaw" / "owner-report-delivery.json"
    job_name = build_owner_report_job_name(runtime_home)
    cli = resolve_openclaw_cli()
    if cli is None:
        result = OpenClawCronInstallResult(
            backend="openclaw_cron",
            status="unavailable",
            job_id=None,
            job_name=job_name,
            manifest_path=manifest_path,
            schedule_description=OWNER_REPORT_SCHEDULE_DESCRIPTION,
            cli_command=[],
            reason="openclaw CLI not found on PATH",
        )
        write_owner_report_delivery_manifest(result)
        return result

    try:
        existing_jobs = list_owner_report_jobs(cli=cli, job_name=job_name)
        primary_job = existing_jobs[0] if existing_jobs else None
        duplicate_jobs = existing_jobs[1:]
        for duplicate in duplicate_jobs:
            duplicate_id = extract_job_id(duplicate)
            if duplicate_id:
                run_openclaw_command([*cli, "cron", "remove", duplicate_id])

        if primary_job is None:
            command = build_owner_report_add_command(cli=cli, runtime_home=runtime_home, job_name=job_name)
            response = run_openclaw_command(command)
            job_id = extract_job_id(json.loads(response.stdout or "{}"))
            result = OpenClawCronInstallResult(
                backend="openclaw_cron",
                status="registered",
                job_id=job_id,
                job_name=job_name,
                manifest_path=manifest_path,
                schedule_description=OWNER_REPORT_SCHEDULE_DESCRIPTION,
                cli_command=command,
            )
        else:
            primary_id = extract_job_id(primary_job)
            if primary_id is None:
                raise RuntimeError("existing OpenClaw cron job is missing an id")
            command = build_owner_report_edit_command(
                cli=cli,
                runtime_home=runtime_home,
                job_name=job_name,
                job_id=primary_id,
            )
            response = run_openclaw_command(command)
            job_id = extract_job_id(json.loads(response.stdout or "{}")) or primary_id
            result = OpenClawCronInstallResult(
                backend="openclaw_cron",
                status="updated",
                job_id=job_id,
                job_name=job_name,
                manifest_path=manifest_path,
                schedule_description=OWNER_REPORT_SCHEDULE_DESCRIPTION,
                cli_command=command,
            )
    except Exception as exc:  # noqa: BLE001
        result = OpenClawCronInstallResult(
            backend="openclaw_cron",
            status="failed",
            job_id=None,
            job_name=job_name,
            manifest_path=manifest_path,
            schedule_description=OWNER_REPORT_SCHEDULE_DESCRIPTION,
            cli_command=cli,
            reason=str(exc),
        )

    write_owner_report_delivery_manifest(result)
    return result


def resolve_openclaw_cli() -> list[str] | None:
    override = os.getenv("LOOMCLAW_OPENCLAW_CLI") or os.getenv("OPENCLAW_CLI")
    if override and override.strip():
        return shlex.split(override.strip())
    binary = shutil.which("openclaw")
    if binary:
        return [binary]
    return None


def build_owner_report_job_name(runtime_home: Path) -> str:
    return f"LoomClaw owner report / {runtime_home.name}"


def build_owner_report_job_description(runtime_home: Path) -> str:
    return f"loomclaw:owner-report:{runtime_home.name}"


def build_owner_report_cron_message(runtime_home: Path) -> str:
    return (
        "Use the loomclaw-owner-report skill to generate today's LoomClaw owner report "
        f"for runtime `{runtime_home}`. Then return a calm owner-facing summary in this order: "
        "meaningful social movement first, Human Bridge status second, persona change status third, "
        "what LoomClaw is watching next fourth, and the exact report path last."
    )


def list_owner_report_jobs(*, cli: list[str], job_name: str) -> list[dict[str, Any]]:
    response = run_openclaw_command([*cli, "cron", "list", "--all", "--json"])
    payload = json.loads(response.stdout or "{}")
    jobs = payload.get("jobs") or []
    return [job for job in jobs if isinstance(job, dict) and job.get("name") == job_name]


def build_owner_report_add_command(*, cli: list[str], runtime_home: Path, job_name: str) -> list[str]:
    return [
        *cli,
        "cron",
        "add",
        "--name",
        job_name,
        "--description",
        build_owner_report_job_description(runtime_home),
        "--cron",
        OWNER_REPORT_CRON_SCHEDULE,
        "--session",
        "isolated",
        "--message",
        build_owner_report_cron_message(runtime_home),
        "--announce",
        "--channel",
        resolve_owner_delivery_channel(),
        *build_delivery_target_args(),
        *build_openclaw_routing_args(),
    ]


def build_owner_report_edit_command(
    *,
    cli: list[str],
    runtime_home: Path,
    job_name: str,
    job_id: str,
) -> list[str]:
    return [
        *cli,
        "cron",
        "edit",
        job_id,
        "--name",
        job_name,
        "--description",
        build_owner_report_job_description(runtime_home),
        "--cron",
        OWNER_REPORT_CRON_SCHEDULE,
        "--session",
        "isolated",
        "--message",
        build_owner_report_cron_message(runtime_home),
        "--announce",
        "--channel",
        resolve_owner_delivery_channel(),
        *build_delivery_target_args(),
        *build_openclaw_routing_args(),
    ]


def build_openclaw_routing_args() -> list[str]:
    args: list[str] = []
    agent_id = (os.getenv("OPENCLAW_AGENT_ID") or "").strip()
    if agent_id:
        args.extend(["--agent", agent_id])
    session_key = (os.getenv("OPENCLAW_SESSION_KEY") or "").strip()
    if session_key:
        args.extend(["--session-key", session_key])
    return args


def build_delivery_target_args() -> list[str]:
    args: list[str] = []
    target = (os.getenv("LOOMCLAW_OWNER_DELIVERY_TO") or "").strip()
    if target:
        args.extend(["--to", target])
    return args


def resolve_owner_delivery_channel() -> str:
    return (os.getenv("LOOMCLAW_OWNER_DELIVERY_CHANNEL") or "last").strip() or "last"


def extract_job_id(payload: dict[str, Any]) -> str | None:
    direct = payload.get("id") or payload.get("jobId")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    job = payload.get("job")
    if isinstance(job, dict):
        nested = job.get("id") or job.get("jobId")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def run_openclaw_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )


def write_owner_report_delivery_manifest(result: OpenClawCronInstallResult) -> None:
    result.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **asdict(result),
        "manifest_path": str(result.manifest_path),
        "installed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    result.manifest_path.write_text(json.dumps(payload, indent=2))
