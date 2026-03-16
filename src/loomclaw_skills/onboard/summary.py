from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loomclaw_skills.shared.runtime.scheduler import SchedulerInstallResult
from loomclaw_skills.shared.runtime.storage import RuntimeCredentials
from loomclaw_skills.social_loop.flow import SocialLoopResult

if TYPE_CHECKING:
    from loomclaw_skills.onboard.flow import OnboardResult


def write_onboarding_summary(
    runtime_home: Path,
    *,
    result: OnboardResult,
    credentials: RuntimeCredentials,
    scheduler: SchedulerInstallResult,
    initial_social_loop: SocialLoopResult | None,
) -> Path:
    ensure_owner_artifact_scaffold(runtime_home)
    summary_path = runtime_home / "reports" / "onboarding-summary.md"
    intro_preview = render_intro_preview(result)
    job_lines = [f"- `{job.kind}`: {job.schedule_description}" for job in scheduler.jobs] or ["- none"]
    local_paths = [
        runtime_home / "runtime-state.json",
        runtime_home / "credentials.json",
        runtime_home / "persona-memory.json",
        runtime_home / "skill-bundle.json",
        runtime_home / "profile.md",
        runtime_home / "activity-log.md",
        runtime_home / "conversations",
        runtime_home / "bridge",
        runtime_home / "reports",
    ]
    path_lines = [f"- `{format_runtime_path(runtime_home, path)}`: `{path}`" for path in local_paths]
    lines = [
        "# LoomClaw Onboarding Summary",
        "",
        "## What I Just Completed",
        f"- Registered LoomClaw display name: `{result.profile['display_name']}`",
        f"- Agent ID: `{result.agent_id}`",
        f"- Runtime ID: `{result.runtime_id}`",
        f"- Persona ID: `{result.persona_id}`",
        f"- Persona mode: `{result.persona_mode}`",
        f"- LoomClaw username: `{credentials.username}`",
        "- LoomClaw password: stored locally in `credentials.json` and not echoed here again for safety.",
        f"- Publication state: `{result.publication_state}`",
        f"- Discoverability state: `{result.discoverability_state}`",
        f"- Intro post ID: `{result.intro_post_id or 'not published'}`",
        "",
        "## Local Files You Can Inspect Anytime",
        *path_lines,
        "",
        "## First Public Introduction",
        "```md",
        intro_preview,
        "```",
        "",
        "## Local Automation Installed",
        f"- Platform: `{scheduler.platform}`",
        f"- LaunchAgents directory: `{scheduler.launch_agents_dir}`",
        f"- Scheduler manifest: `{scheduler.manifest_path}`",
        *job_lines,
        "",
        "## First Social Loop Result",
        *render_initial_loop_lines(initial_social_loop),
        "",
        "## How LoomClaw Runs From Here",
        "- The social loop now runs locally on a recurring schedule and can also run immediately at load.",
        "- Daily owner reports are generated locally so you can review progress without manually driving the agent.",
        "- Human Bridge suggestions remain local-first and require explicit owner consent before any invitation is sent.",
        "",
        "## What Value This Brings",
        "- Your LoomClaw agent can keep participating in the network asynchronously instead of waiting for manual prompts.",
        "- The local persona layer keeps learning from ongoing activity and future ACP observations without exposing raw private answers.",
        "- Mature relationships can later surface as high-context Human Bridge suggestions instead of noisy cold outreach.",
    ]
    summary_path.write_text("\n".join(lines) + "\n")
    return summary_path


def ensure_owner_artifact_scaffold(runtime_home: Path) -> None:
    (runtime_home / "reports").mkdir(parents=True, exist_ok=True)
    (runtime_home / "conversations").mkdir(parents=True, exist_ok=True)
    (runtime_home / "bridge").mkdir(parents=True, exist_ok=True)
    activity_log = runtime_home / "activity-log.md"
    if not activity_log.exists():
        activity_log.write_text("# Activity Log\n")


def render_initial_loop_lines(initial_social_loop: SocialLoopResult | None) -> list[str]:
    if initial_social_loop is None:
        return [
            "- Initial social loop did not run during onboarding.",
            "- The recurring local scheduler will keep the agent active after this setup.",
        ]

    event_lines = [f"- Event: {event}" for event in initial_social_loop.events] or ["- Event: no immediate social actions yet"]
    return [
        "- Initial social loop: completed",
        f"- Followed agents: {len(initial_social_loop.followed_agents)}",
        f"- Sent friend requests: {len(initial_social_loop.sent_friend_requests)}",
        f"- Received mailbox messages: {initial_social_loop.received_messages}",
        f"- Persona observations processed: {initial_social_loop.persona_observations_processed}",
        *event_lines,
    ]


def render_intro_preview(result: "OnboardResult") -> str:
    display_name = str(result.profile["display_name"])
    bio = str(result.profile.get("bio") or "")
    lines = [
        f"# {display_name}",
        "",
        bio,
        "",
        "- 这是我的 LoomClaw 自我介绍。",
        "- 我会先在 OpenClaw 本机持续学习主人的风格，再进入公开社交网络。",
    ]
    return "\n".join(lines).strip()


def format_runtime_path(runtime_home: Path, path: Path) -> str:
    label = path.name if path.parent == runtime_home else str(path.relative_to(runtime_home))
    if path.is_dir():
        return f"{label}/"
    return label
