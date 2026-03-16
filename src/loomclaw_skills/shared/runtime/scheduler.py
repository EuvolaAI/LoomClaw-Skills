from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    kind: str
    label: str
    plist_path: Path
    installed_plist_path: Path
    schedule_description: str
    run_at_load: bool


@dataclass(frozen=True, slots=True)
class SchedulerInstallResult:
    platform: str
    launch_agents_dir: Path
    manifest_path: Path
    jobs: list[ScheduledJob]


def install_local_scheduler(
    runtime_home: Path,
    *,
    base_url: str,
    python_executable: str | None = None,
    platform_name: str | None = None,
) -> SchedulerInstallResult:
    platform = platform_name or sys.platform
    if platform != "darwin":
        raise RuntimeError(f"unsupported platform for local LoomClaw scheduler: {platform}")

    launch_agents_dir = resolve_launch_agents_dir()
    launch_agents_dir.mkdir(parents=True, exist_ok=True)

    launchd_dir = runtime_home / "launchd"
    launchd_dir.mkdir(parents=True, exist_ok=True)

    python_bin = python_executable or sys.executable
    runtime_slug = sanitize_label_component(runtime_home.name or "runtime")
    jobs = build_jobs(
        runtime_slug=runtime_slug,
        runtime_home=runtime_home,
        base_url=base_url,
        python_executable=python_bin,
        launchd_dir=launchd_dir,
        launch_agents_dir=launch_agents_dir,
    )
    write_launchd_plists(jobs)
    sync_launch_agents(jobs)
    bootstrap_launch_agents(jobs)

    manifest_path = launchd_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "platform": platform,
                "runtime_home": str(runtime_home),
                "launch_agents_dir": str(launch_agents_dir),
                "base_url": base_url,
                "installed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "jobs": [
                    {
                        "kind": job.kind,
                        "label": job.label,
                        "plist_path": str(job.plist_path),
                        "installed_plist_path": str(job.installed_plist_path),
                        "schedule_description": job.schedule_description,
                        "run_at_load": job.run_at_load,
                    }
                    for job in jobs
                ],
            },
            indent=2,
        )
    )
    return SchedulerInstallResult(
        platform=platform,
        launch_agents_dir=launch_agents_dir,
        manifest_path=manifest_path,
        jobs=jobs,
    )


def build_jobs(
    *,
    runtime_slug: str,
    runtime_home: Path,
    base_url: str,
    python_executable: str,
    launchd_dir: Path,
    launch_agents_dir: Path,
) -> list[ScheduledJob]:
    skills_root = Path(__file__).resolve().parents[4]
    definitions = [
        {
            "kind": "social_loop",
            "suffix": "social-loop",
            "script_path": skills_root / "loomclaw-social-loop" / "scripts" / "run_loop.py",
            "schedule_description": "every 30 minutes (run at load)",
            "run_at_load": True,
            "start_interval": 1800,
        },
        {
            "kind": "owner_report",
            "suffix": "owner-report",
            "script_path": skills_root / "loomclaw-owner-report" / "scripts" / "generate_report.py",
            "schedule_description": "every day at 20:00 local time",
            "run_at_load": False,
            "start_calendar_interval": {"Hour": 20, "Minute": 0},
        },
        {
            "kind": "bridge_sync",
            "suffix": "bridge-sync",
            "script_path": skills_root / "loomclaw-human-bridge" / "scripts" / "invitations.py",
            "schedule_description": "every 15 minutes (run at load)",
            "run_at_load": True,
            "start_interval": 900,
        },
    ]
    jobs: list[ScheduledJob] = []
    for definition in definitions:
        filename = f"ai.euvola.loomclaw.{runtime_slug}.{definition['suffix']}.plist"
        jobs.append(
            ScheduledJob(
                kind=definition["kind"],
                label=f"ai.euvola.loomclaw.{runtime_slug}.{definition['suffix']}",
                plist_path=launchd_dir / filename,
                installed_plist_path=launch_agents_dir / filename,
                schedule_description=definition["schedule_description"],
                run_at_load=definition["run_at_load"],
            )
        )
    for job, definition in zip(jobs, definitions, strict=True):
        payload = build_plist_payload(
            label=job.label,
            python_executable=python_executable,
            script_path=definition["script_path"],
            runtime_home=runtime_home,
            base_url=base_url,
            run_at_load=job.run_at_load,
            start_interval=definition.get("start_interval"),
            start_calendar_interval=definition.get("start_calendar_interval"),
        )
        job.plist_path.write_bytes(plistlib.dumps(payload))
    return jobs


def build_plist_payload(
    *,
    label: str,
    python_executable: str,
    script_path: Path,
    runtime_home: Path,
    base_url: str,
    run_at_load: bool,
    start_interval: int | None = None,
    start_calendar_interval: dict[str, int] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "Label": label,
        "ProgramArguments": [
            python_executable,
            str(script_path),
            "--runtime-home",
            str(runtime_home),
            "--base-url",
            base_url,
        ],
        "RunAtLoad": run_at_load,
        "WorkingDirectory": str(runtime_home),
        "StandardOutPath": str(runtime_home / "logs" / f"{label}.out.log"),
        "StandardErrorPath": str(runtime_home / "logs" / f"{label}.err.log"),
        "EnvironmentVariables": {
            "LOOMCLAW_BASE_URL": base_url,
            "PYTHONUNBUFFERED": "1",
        },
    }
    if start_interval is not None:
        payload["StartInterval"] = start_interval
    if start_calendar_interval is not None:
        payload["StartCalendarInterval"] = start_calendar_interval
    return payload


def write_launchd_plists(jobs: list[ScheduledJob]) -> None:
    for job in jobs:
        job.plist_path.parent.mkdir(parents=True, exist_ok=True)
        logs_dir = job.plist_path.parent.parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)


def sync_launch_agents(jobs: list[ScheduledJob]) -> None:
    for job in jobs:
        job.installed_plist_path.parent.mkdir(parents=True, exist_ok=True)
        job.installed_plist_path.write_bytes(job.plist_path.read_bytes())


def bootstrap_launch_agents(jobs: list[ScheduledJob]) -> None:
    domain = f"gui/{os.getuid()}"
    for job in jobs:
        try:
            run_launchctl_command(["launchctl", "bootout", domain, str(job.installed_plist_path)])
        except subprocess.CalledProcessError:
            pass
        run_launchctl_command(["launchctl", "bootstrap", domain, str(job.installed_plist_path)])


def run_launchctl_command(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True, text=True)


def resolve_launch_agents_dir() -> Path:
    override = os.getenv("LOOMCLAW_LAUNCH_AGENTS_DIR")
    if override and override.strip():
        return Path(override.strip()).expanduser()
    return Path.home() / "Library" / "LaunchAgents"


def sanitize_label_component(value: str) -> str:
    lowered = value.lower().strip()
    sanitized = "".join(ch if ch.isalnum() else "-" for ch in lowered)
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    return sanitized.strip("-") or "runtime"
