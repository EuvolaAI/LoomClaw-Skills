from __future__ import annotations

import json
import os
import plistlib
import re
import shlex
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
    scheduler_backend: str = "launchd"


@dataclass(frozen=True, slots=True)
class SchedulerInstallResult:
    platform: str
    launch_agents_dir: Path
    manifest_path: Path
    jobs: list[ScheduledJob]
    scheduler_backend: str = "launchd"


def install_local_scheduler(
    runtime_home: Path,
    *,
    base_url: str,
    python_executable: str | None = None,
    platform_name: str | None = None,
) -> SchedulerInstallResult:
    platform = platform_name or sys.platform
    python_bin = python_executable or sys.executable
    runtime_slug = sanitize_label_component(runtime_home.name or "runtime")
    if platform == "darwin":
        scheduler_backend = "launchd"
        install_root = resolve_launch_agents_dir()
        install_root.mkdir(parents=True, exist_ok=True)
        definition_dir = runtime_home / "launchd"
        definition_dir.mkdir(parents=True, exist_ok=True)
        manifest_root = definition_dir
        jobs = build_launchd_jobs(
            runtime_slug=runtime_slug,
            runtime_home=runtime_home,
            base_url=base_url,
            python_executable=python_bin,
            definition_dir=definition_dir,
            install_root=install_root,
        )
        write_launchd_plists(jobs)
        sync_launch_agents(jobs)
        bootstrap_launch_agents(jobs)
    elif platform.startswith("linux"):
        scheduler_backend = "cron"
        install_root = resolve_linux_cron_dir(runtime_home)
        install_root.mkdir(parents=True, exist_ok=True)
        manifest_root = install_root
        jobs = build_cron_jobs(
            runtime_slug=runtime_slug,
            runtime_home=runtime_home,
            base_url=base_url,
            python_executable=python_bin,
            cron_dir=install_root,
        )
        write_cron_job_files(jobs)
        install_linux_crontab(runtime_slug=runtime_slug, jobs=jobs)
    else:
        raise RuntimeError(f"unsupported platform for local LoomClaw scheduler: {platform}")

    manifest_path = manifest_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "platform": platform,
                "scheduler_backend": scheduler_backend,
                "runtime_home": str(runtime_home),
                "launch_agents_dir": str(install_root),
                "install_root": str(install_root),
                "base_url": base_url,
                "installed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "jobs": [
                    {
                        "kind": job.kind,
                        "label": job.label,
                        "scheduler_backend": job.scheduler_backend,
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
        launch_agents_dir=install_root,
        manifest_path=manifest_path,
        jobs=jobs,
        scheduler_backend=scheduler_backend,
    )


def build_job_definitions() -> list[dict[str, object]]:
    skills_root = Path(__file__).resolve().parents[4]
    managed_runner = skills_root / "loomclaw-onboard" / "scripts" / "run_managed_skill.py"
    return [
        {
            "kind": "social_loop",
            "suffix": "social-loop",
            "script_path": managed_runner,
            "script_args": ["--kind", "social_loop"],
            "schedule_description": "every 60 minutes (run at load)",
            "run_at_load": True,
            "start_interval": 3600,
            "cron_schedule": "0 * * * *",
        },
        {
            "kind": "owner_report",
            "suffix": "owner-report",
            "script_path": managed_runner,
            "script_args": ["--kind", "owner_report"],
            "schedule_description": "every day at 20:00 local time",
            "run_at_load": False,
            "start_calendar_interval": {"Hour": 20, "Minute": 0},
            "cron_schedule": "0 20 * * *",
        },
        {
            "kind": "bridge_loop",
            "suffix": "bridge-loop",
            "script_path": managed_runner,
            "script_args": ["--kind", "bridge_loop"],
            "schedule_description": "every 4 hours (run at load)",
            "run_at_load": True,
            "start_interval": 14400,
            "cron_schedule": "0 */4 * * *",
        },
        {
            "kind": "bundle_update",
            "suffix": "bundle-update",
            "script_path": managed_runner,
            "script_args": ["--kind", "bundle_update"],
            "schedule_description": "every day at 03:17 local time",
            "run_at_load": False,
            "start_calendar_interval": {"Hour": 3, "Minute": 17},
            "cron_schedule": "17 3 * * *",
        },
    ]


def build_launchd_jobs(
    *,
    runtime_slug: str,
    runtime_home: Path,
    base_url: str,
    python_executable: str,
    definition_dir: Path,
    install_root: Path,
) -> list[ScheduledJob]:
    definitions = build_job_definitions()
    jobs: list[ScheduledJob] = []
    for definition in definitions:
        filename = f"ai.euvola.loomclaw.{runtime_slug}.{definition['suffix']}.plist"
        jobs.append(
            ScheduledJob(
                kind=definition["kind"],
                label=f"ai.euvola.loomclaw.{runtime_slug}.{definition['suffix']}",
                plist_path=definition_dir / filename,
                installed_plist_path=install_root / filename,
                schedule_description=definition["schedule_description"],
                run_at_load=definition["run_at_load"],
                scheduler_backend="launchd",
            )
        )
    for job, definition in zip(jobs, definitions, strict=True):
        payload = build_plist_payload(
            label=job.label,
            python_executable=python_executable,
            script_path=definition["script_path"],
            script_args=list(definition.get("script_args", [])),
            runtime_home=runtime_home,
            base_url=base_url,
            run_at_load=job.run_at_load,
            start_interval=definition.get("start_interval"),
            start_calendar_interval=definition.get("start_calendar_interval"),
        )
        job.plist_path.write_bytes(plistlib.dumps(payload))
    return jobs


def build_cron_jobs(
    *,
    runtime_slug: str,
    runtime_home: Path,
    base_url: str,
    python_executable: str,
    cron_dir: Path,
) -> list[ScheduledJob]:
    definitions = build_job_definitions()
    jobs: list[ScheduledJob] = []
    for definition in definitions:
        filename = f"ai.euvola.loomclaw.{runtime_slug}.{definition['suffix']}.cron"
        job_path = cron_dir / filename
        jobs.append(
            ScheduledJob(
                kind=definition["kind"],
                label=f"ai.euvola.loomclaw.{runtime_slug}.{definition['suffix']}",
                plist_path=job_path,
                installed_plist_path=job_path,
                schedule_description=definition["schedule_description"],
                run_at_load=definition["run_at_load"],
                scheduler_backend="cron",
            )
        )
    for job, definition in zip(jobs, definitions, strict=True):
        job.plist_path.write_text(
            render_cron_line(
                schedule=str(definition["cron_schedule"]),
                python_executable=python_executable,
                script_path=Path(definition["script_path"]),
                script_args=list(definition.get("script_args", [])),
                runtime_home=runtime_home,
                base_url=base_url,
                label=job.label,
            )
            + "\n"
        )
    return jobs


def build_plist_payload(
    *,
    label: str,
    python_executable: str,
    script_path: Path,
    script_args: list[str],
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
            *script_args,
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


def render_cron_line(
    *,
    schedule: str,
    python_executable: str,
    script_path: Path,
    script_args: list[str],
    runtime_home: Path,
    base_url: str,
    label: str,
) -> str:
    logs_dir = runtime_home / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    command_parts = [
        f"LOOMCLAW_BASE_URL={shlex.quote(base_url)}",
        "PYTHONUNBUFFERED=1",
        shlex.quote(python_executable),
        shlex.quote(str(script_path)),
        *[shlex.quote(arg) for arg in script_args],
        "--runtime-home",
        shlex.quote(str(runtime_home)),
        "--base-url",
        shlex.quote(base_url),
    ]
    stdout_path = logs_dir / f"{label}.out.log"
    stderr_path = logs_dir / f"{label}.err.log"
    command = " ".join(command_parts)
    return f"{schedule} {command} >> {shlex.quote(str(stdout_path))} 2>> {shlex.quote(str(stderr_path))}"


def write_launchd_plists(jobs: list[ScheduledJob]) -> None:
    for job in jobs:
        job.plist_path.parent.mkdir(parents=True, exist_ok=True)
        logs_dir = job.plist_path.parent.parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)


def write_cron_job_files(jobs: list[ScheduledJob]) -> None:
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


def install_linux_crontab(*, runtime_slug: str, jobs: list[ScheduledJob]) -> None:
    existing = read_user_crontab()
    managed_block = render_managed_crontab_block(runtime_slug=runtime_slug, jobs=jobs)
    merged = replace_managed_crontab_block(existing=existing, runtime_slug=runtime_slug, managed_block=managed_block)
    bundle_path = jobs[0].plist_path.parent / "loomclaw.crontab"
    bundle_path.write_text(merged)
    install_user_crontab(merged)


def read_user_crontab() -> str:
    completed = subprocess.run(
        ["crontab", "-l"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0:
        return completed.stdout
    stderr = (completed.stderr or "").lower()
    if "no crontab" in stderr:
        return ""
    raise subprocess.CalledProcessError(
        completed.returncode,
        completed.args,
        output=completed.stdout,
        stderr=completed.stderr,
    )


def install_user_crontab(content: str) -> None:
    subprocess.run(
        ["crontab", "-"],
        input=content,
        check=True,
        capture_output=True,
        text=True,
    )


def render_managed_crontab_block(*, runtime_slug: str, jobs: list[ScheduledJob]) -> str:
    begin = crontab_marker("BEGIN", runtime_slug)
    end = crontab_marker("END", runtime_slug)
    lines = [begin]
    lines.extend(job.plist_path.read_text().strip() for job in jobs)
    lines.append(end)
    return "\n".join(lines)


def replace_managed_crontab_block(*, existing: str, runtime_slug: str, managed_block: str) -> str:
    begin = re.escape(crontab_marker("BEGIN", runtime_slug))
    end = re.escape(crontab_marker("END", runtime_slug))
    pattern = re.compile(rf"{begin}\n.*?\n{end}\n?", re.DOTALL)
    existing_clean = existing.strip()
    if pattern.search(existing_clean):
        replaced = pattern.sub(managed_block, existing_clean)
    elif existing_clean:
        replaced = existing_clean + "\n\n" + managed_block
    else:
        replaced = managed_block
    return replaced.strip() + "\n"


def crontab_marker(kind: str, runtime_slug: str) -> str:
    return f"# LOOMCLAW-{kind} {runtime_slug}"


def resolve_launch_agents_dir() -> Path:
    override = os.getenv("LOOMCLAW_LAUNCH_AGENTS_DIR")
    if override and override.strip():
        return Path(override.strip()).expanduser()
    return Path.home() / "Library" / "LaunchAgents"


def resolve_linux_cron_dir(runtime_home: Path) -> Path:
    override = os.getenv("LOOMCLAW_CRON_DIR")
    if override and override.strip():
        return Path(override.strip()).expanduser()
    return runtime_home / "cron"


def sanitize_label_component(value: str) -> str:
    lowered = value.lower().strip()
    sanitized = "".join(ch if ch.isalnum() else "-" for ch in lowered)
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    return sanitized.strip("-") or "runtime"
