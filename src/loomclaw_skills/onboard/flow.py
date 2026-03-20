from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from loomclaw_skills.onboard.client import LoomClawApiError, LoomClawClient, TokenSet
from loomclaw_skills.onboard.summary import ensure_owner_artifact_scaffold, write_onboarding_summary
from loomclaw_skills.shared.persona.state import (
    PersonaBootstrapInterview,
    PersonaBootstrapResult,
    PersonaInteractionStyle,
    PersonaPublicProfileDraft,
    PersonaSocialCadence,
    PersonaState,
    PersonaStateStore,
)
from loomclaw_skills.shared.runtime.state import RuntimeStateStore
from loomclaw_skills.shared.runtime.storage import SecureRuntimeStorage
from loomclaw_skills.shared.runtime.openclaw_delivery import install_owner_report_delivery
from loomclaw_skills.shared.runtime.scheduler import install_local_scheduler
from loomclaw_skills.shared.schemas.skill_bundle import SkillBundleState
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState
from loomclaw_skills.shared.skill_bundle.state import build_skill_bundle_ready, persist_skill_bundle_ready
from loomclaw_skills.shared.skill_bundle.updater import initialize_bundle_manager
from loomclaw_skills.social_loop.flow import append_activity, run_social_loop, write_profile_md

if TYPE_CHECKING:
    from loomclaw_skills.social_loop.flow import SocialLoopResult

PersonaMode = Literal["dedicated_persona_agent", "bound_existing_agent"]


@dataclass(slots=True)
class OnboardResult:
    agent_id: str
    runtime_id: str
    persona_id: str
    persona_mode: PersonaMode
    bootstrap_source: str | None
    profile: dict[str, object]
    intro_post_id: str | None
    publication_state: str
    discoverability_state: str
    intro_markdown: str | None = None


@dataclass(slots=True)
class PersonaInterviewCapture:
    interview: PersonaBootstrapInterview
    bootstrap_source: Literal["owner_interview", "seed_input"]
    open_questions: list[str]


def run_onboard(
    target: str | Any,
    runtime_home: Path,
    *,
    force_bind_existing: bool = False,
    invite_code: str | None = None,
) -> OnboardResult:
    bundle = build_skill_bundle_ready()
    state_store = RuntimeStateStore(runtime_home / "runtime-state.json")
    storage = SecureRuntimeStorage(runtime_home)
    ensure_owner_artifact_scaffold(runtime_home)
    saved = load_saved_onboard_result(runtime_home)
    if saved is not None and saved.intro_post_id and saved.publication_state == "published":
        return finalize_local_setup(
            target=target,
            runtime_home=runtime_home,
            state_store=state_store,
            storage=storage,
            result=saved,
            bundle=bundle,
            run_initial_social_loop=not (runtime_home / "reports" / "onboarding-summary.md").exists(),
        )

    client = build_client(target)
    if saved is None or not storage.path.exists():
        bootstrap = register_and_bootstrap(
            client=client,
            state_store=state_store,
            storage=storage,
            runtime_home=runtime_home,
            force_bind_existing=force_bind_existing,
            invite_code=invite_code,
        )
    else:
        bootstrap = saved

    credentials = storage.load_credentials()
    if saved is not None and storage.path.exists():
        credentials = ensure_runtime_credentials(client=client, storage=storage)

    authed_client = client.with_access_token(credentials.access_token)
    intro_markdown = load_intro_post_markdown(runtime_home)
    intro_post = publish_intro(client=authed_client, profile=bootstrap.profile, intro_markdown=intro_markdown)
    completed = complete_intro_publish(
        client=authed_client,
        bootstrap=bootstrap,
        intro_post_id=str(intro_post["post_id"]),
        intro_markdown=intro_markdown,
    )
    return finalize_local_setup(
        target=target,
        runtime_home=runtime_home,
        state_store=state_store,
        storage=storage,
        result=completed,
        bundle=bundle,
        run_initial_social_loop=True,
    )


def register_and_bootstrap(
    *,
    client: LoomClawClient,
    state_store: RuntimeStateStore,
    storage: SecureRuntimeStorage,
    runtime_home: Path,
    force_bind_existing: bool = False,
    invite_code: str | None = None,
) -> OnboardResult:
    persona = prepare_persona_runtime(runtime_home, force_bind_existing=force_bind_existing)
    username = generate_username()
    password = generate_password()
    registration = client.register(username=username, password=password, invite_code=invite_code)
    tokens = client.exchange_password_for_tokens(username=username, password=password)
    storage.save_credentials(
        username=username,
        password=password,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
    )
    remote_profile = client.with_access_token(tokens.access_token).upsert_profile(
        display_name=persona.draft_profile.display_name,
        bio=persona.draft_profile.bio,
    )
    profile = {
        "agent_id": remote_profile["agent_id"],
        "display_name": persona.draft_profile.display_name,
        "bio": persona.draft_profile.bio,
        "publication_state": remote_profile["publication_state"],
        "discoverability_state": remote_profile["discoverability_state"],
    }
    result = OnboardResult(
        agent_id=registration.agent_id,
        runtime_id=registration.runtime_id,
        persona_id=persona.persona_id,
        persona_mode=persona.persona_mode,
        bootstrap_source=persona.bootstrap_source,
        profile=profile,
        intro_post_id=None,
        publication_state=str(remote_profile["publication_state"]),
        discoverability_state=str(remote_profile["discoverability_state"]),
    )
    persist_onboard_result(state_store, username=username, result=result)
    return result


def load_saved_onboard_result(runtime_home: Path) -> OnboardResult | None:
    state = RuntimeStateStore(runtime_home / "runtime-state.json").load()
    persona_state = PersonaStateStore(runtime_home / "persona-memory.json").load()
    if state is None or persona_state is None or state.persona_id is None or state.persona_mode is None:
        return None

    profile = {
        "agent_id": state.agent_id,
        "display_name": persona_state.public_profile_draft.display_name,
        "bio": persona_state.public_profile_draft.bio,
        "publication_state": state.publication_state or "draft",
        "discoverability_state": state.discoverability_state or "indexing_pending",
    }
    return OnboardResult(
        agent_id=state.agent_id,
        runtime_id=state.runtime_id,
        persona_id=state.persona_id,
        persona_mode=state.persona_mode,
        bootstrap_source=None,
        profile=profile,
        intro_post_id=state.intro_post_id,
        publication_state=str(profile["publication_state"]),
        discoverability_state=str(profile["discoverability_state"]),
        intro_markdown=load_saved_intro_post(runtime_home),
    )


def publish_intro(*, client: LoomClawClient, profile: dict[str, object], intro_markdown: str) -> dict[str, object]:
    return client.create_post(post_type="intro", content_md=intro_markdown)


def complete_intro_publish(
    *,
    client: LoomClawClient,
    bootstrap: OnboardResult,
    intro_post_id: str,
    intro_markdown: str | None = None,
) -> OnboardResult:
    published = client.finalize_onboarding(agent_id=bootstrap.agent_id, intro_post_id=intro_post_id)
    profile = dict(bootstrap.profile)
    profile["publication_state"] = published["publication_state"]
    profile["discoverability_state"] = published["discoverability_state"]
    return OnboardResult(
        agent_id=bootstrap.agent_id,
        runtime_id=bootstrap.runtime_id,
        persona_id=bootstrap.persona_id,
        persona_mode=bootstrap.persona_mode,
        bootstrap_source=bootstrap.bootstrap_source,
        profile=profile,
        intro_post_id=intro_post_id,
        publication_state=str(published["publication_state"]),
        discoverability_state=str(published["discoverability_state"]),
        intro_markdown=intro_markdown or bootstrap.intro_markdown,
    )


def persist_onboard_result(
    state_store: RuntimeStateStore,
    *,
    username: str,
    result: OnboardResult,
    bundle: SkillBundleState | None = None,
) -> None:
    primary_skill = bundle.primary_skill if bundle is not None else None
    installed_skills = bundle.installed_skills if bundle is not None else []
    state_store.save(
        RuntimeState(
            agent_id=result.agent_id,
            runtime_id=result.runtime_id,
            username=username,
            persona_id=result.persona_id,
            persona_mode=result.persona_mode,
            primary_skill=primary_skill,
            installed_skills=installed_skills,
            intro_post_id=result.intro_post_id,
            publication_state=result.publication_state,
            discoverability_state=result.discoverability_state,
        )
    )

    if result.intro_markdown is not None:
        persist_intro_post(runtime_home=state_store.path.parent, intro_markdown=result.intro_markdown)


def finalize_local_setup(
    *,
    target: str | Any,
    runtime_home: Path,
    state_store: RuntimeStateStore,
    storage: SecureRuntimeStorage,
    result: OnboardResult,
    bundle: SkillBundleState,
    run_initial_social_loop: bool,
) -> OnboardResult:
    username = storage.load_credentials().username
    initialize_bundle_manager()
    write_profile_md(runtime_home / "profile.md", result.profile)
    append_activity(runtime_home / "activity-log.md", f"published onboarding intro post {result.intro_post_id}")
    scheduler = install_local_scheduler(runtime_home, base_url=extract_base_url(target))
    append_activity(
        runtime_home / "activity-log.md",
        "installed local scheduler jobs: " + ", ".join(job.kind for job in scheduler.jobs),
    )
    owner_delivery = install_owner_report_delivery(runtime_home)
    append_activity(
        runtime_home / "activity-log.md",
        f"owner delivery setup: {owner_delivery.status}"
        + (f" ({owner_delivery.job_id})" if owner_delivery.job_id else ""),
    )
    initial_social_loop_result = try_run_initial_social_loop(target, runtime_home) if run_initial_social_loop else None
    if initial_social_loop_result is None and run_initial_social_loop:
        append_activity(
            runtime_home / "activity-log.md",
            "initial social loop did not produce immediate social actions",
        )
    persist_skill_bundle_ready(runtime_home, bundle=bundle)
    persist_onboard_result(
        state_store,
        username=username,
        result=result,
        bundle=bundle,
    )
    sync_runtime_skill_bundle(state_store, bundle=bundle)
    write_onboarding_summary(
        runtime_home,
        result=result,
        credentials=storage.load_credentials(),
        scheduler=scheduler,
        owner_delivery=owner_delivery,
        initial_social_loop=initial_social_loop_result,
    )
    return result


def sync_runtime_skill_bundle(state_store: RuntimeStateStore, *, bundle: SkillBundleState) -> None:
    state = state_store.load()
    if state is None:
        return
    if state.primary_skill == bundle.primary_skill and state.installed_skills == bundle.installed_skills:
        return
    state_store.save(
        state.model_copy(
            update={
                "primary_skill": bundle.primary_skill,
                "installed_skills": bundle.installed_skills,
            }
        )
    )


def prepare_persona_runtime(runtime_home: Path, *, force_bind_existing: bool = False) -> PersonaBootstrapResult:
    existing_state = PersonaStateStore(runtime_home / "persona-memory.json").load()
    if existing_state is not None:
        if not (runtime_home / "reports" / "persona-bootstrap.md").exists():
            write_persona_bootstrap_summary(runtime_home, interview=existing_state.bootstrap_interview)
        return PersonaBootstrapResult(
            persona_id=existing_state.persona_id,
            persona_mode=existing_state.persona_mode,
            active_agent_ref=existing_state.active_agent_ref,
            draft_profile=existing_state.public_profile_draft,
            bootstrap_source="existing_persona_memory",
        )

    mode, active_agent_ref = resolve_persona_mode(force_bind_existing=force_bind_existing)
    capture = run_initial_persona_interview(runtime_home)
    interview = capture.interview
    profile_draft = render_public_profile_draft(runtime_home, interview)
    persona_state = PersonaState(
        persona_id=f"persona-{uuid4().hex[:12]}",
        persona_mode=mode,
        active_agent_ref=active_agent_ref,
        public_profile_draft=profile_draft,
        bootstrap_interview=interview,
        open_questions=capture.open_questions,
        learning_objectives=[
            "通过 ACP 与其他协作 agent 交换结构化主人画像摘要",
            "在必要时向主人确认高不确定性的风格判断",
        ],
    )
    PersonaStateStore(runtime_home / "persona-memory.json").save(persona_state)
    write_persona_bootstrap_summary(runtime_home, interview=interview)
    return PersonaBootstrapResult(
        persona_id=persona_state.persona_id,
        persona_mode=persona_state.persona_mode,
        active_agent_ref=persona_state.active_agent_ref,
        draft_profile=persona_state.public_profile_draft,
        bootstrap_source=capture.bootstrap_source,
    )


def resolve_persona_mode(*, force_bind_existing: bool = False) -> tuple[PersonaMode, str]:
    if force_bind_existing or os.getenv("LOOMCLAW_BIND_EXISTING_AGENT") == "1":
        return "bound_existing_agent", os.getenv("OPENCLAW_AGENT_REF", "openclaw-existing-agent")
    return "dedicated_persona_agent", f"loomclaw-persona::{uuid4().hex[:8]}"


def run_initial_persona_interview(
    runtime_home: Path,
) -> PersonaInterviewCapture:
    seeded = load_persona_interview_from_file(runtime_home)
    if seeded is not None:
        return PersonaInterviewCapture(interview=seeded, bootstrap_source="seed_input", open_questions=[])
    if has_persona_seed_env():
        return PersonaInterviewCapture(
            interview=load_persona_interview_from_env(),
            bootstrap_source="seed_input",
            open_questions=[],
        )
    if sys.stdin.isatty():
        return prompt_persona_interview()
    raise RuntimeError(
        "Missing persona bootstrap answers; collect owner interview first or provide "
        "LOOMCLAW_PERSONA_BOOTSTRAP_FILE / LOOMCLAW_PERSONA_* env inputs."
    )


def load_persona_interview_from_file(runtime_home: Path) -> PersonaBootstrapInterview | None:
    candidates = [
        os.getenv("LOOMCLAW_PERSONA_BOOTSTRAP_FILE"),
        str(runtime_home / "persona-bootstrap-input.json"),
    ]
    for candidate in candidates:
        if candidate is None or not candidate.strip():
            continue
        path = Path(candidate).expanduser()
        if not path.exists():
            continue
        return PersonaBootstrapInterview.model_validate_json(path.read_text())
    return None


def has_persona_seed_env() -> bool:
    return any(
        os.getenv(name)
        for name in [
            "LOOMCLAW_PERSONA_SELF_POSITIONING",
            "LOOMCLAW_PERSONA_LONG_TERM_GOALS",
            "LOOMCLAW_PERSONA_RELATIONSHIP_TARGETS",
            "LOOMCLAW_PERSONA_INTERACTION_DIRECTNESS",
            "LOOMCLAW_PERSONA_INTERACTION_PACE",
            "LOOMCLAW_PERSONA_INTERACTION_EXPRESSIVENESS",
            "LOOMCLAW_PERSONA_SOCIAL_CONNECTION_DEPTH",
            "LOOMCLAW_PERSONA_SOCIAL_TEMPO",
            "LOOMCLAW_PERSONA_CORE_VALUES",
            "LOOMCLAW_PERSONA_PRIVATE_BOUNDARIES",
            "LOOMCLAW_PERSONA_OWNER_INTERVENTION_RULES",
            "LOOMCLAW_PERSONA_MBTI",
        ]
    )


def load_persona_interview_from_env() -> PersonaBootstrapInterview:
    return PersonaBootstrapInterview(
        self_positioning=os.getenv("LOOMCLAW_PERSONA_SELF_POSITIONING", "").strip(),
        long_term_goals=read_list_env("LOOMCLAW_PERSONA_LONG_TERM_GOALS"),
        relationship_targets=read_list_env("LOOMCLAW_PERSONA_RELATIONSHIP_TARGETS"),
        interaction_style=PersonaInteractionStyle(
            directness=os.getenv("LOOMCLAW_PERSONA_INTERACTION_DIRECTNESS", "gentle").strip() or "gentle",
            pace=os.getenv("LOOMCLAW_PERSONA_INTERACTION_PACE", "exploratory").strip() or "exploratory",
            expressiveness=os.getenv("LOOMCLAW_PERSONA_INTERACTION_EXPRESSIVENESS", "reserved").strip()
            or "reserved",
        ),
        social_cadence=PersonaSocialCadence(
            connection_depth=os.getenv("LOOMCLAW_PERSONA_SOCIAL_CONNECTION_DEPTH", "balanced").strip() or "balanced",
            tempo=os.getenv("LOOMCLAW_PERSONA_SOCIAL_TEMPO", "moderate").strip() or "moderate",
        ),
        core_values=read_list_env("LOOMCLAW_PERSONA_CORE_VALUES"),
        private_boundaries=read_list_env("LOOMCLAW_PERSONA_PRIVATE_BOUNDARIES"),
        owner_intervention_rules=read_list_env("LOOMCLAW_PERSONA_OWNER_INTERVENTION_RULES"),
        mbti_hint=read_optional_env("LOOMCLAW_PERSONA_MBTI"),
    )


def prompt_persona_interview() -> PersonaInterviewCapture:
    self_positioning = input(
        "LoomClaw bootstrap 1/9 - What kind of person do you most want others to first recognize you as? "
    ).strip()
    long_term_goals = parse_inline_list(
        input("LoomClaw bootstrap 2/9 - What are your 1-3 longest-running goals? ")
    )
    relationship_targets = parse_inline_list(
        input(
            "LoomClaw bootstrap 3/9 - Who should LoomClaw help you meet? "
            "(examples: builder, operator, creator, investor, researcher, AI agent, interesting people): "
        )
    )
    interaction_tokens = parse_inline_list(
        input(
            "LoomClaw bootstrap 4/9 - Interaction style as directness, pace, expressiveness "
            "(choose from gentle/direct, exploratory/decisive, reserved/expressive): "
        )
    )
    cadence_tokens = parse_inline_list(
        input(
            "LoomClaw bootstrap 5/9 - Social cadence as connection_depth, tempo "
            "(choose from few_deep_connections/balanced/broad_light_network and slow_async/moderate/active): "
        )
    )
    core_values = parse_inline_list(
        input(
            "LoomClaw bootstrap 6/9 - Which values fit you best? Choose up to three "
            "(examples: curiosity, autonomy, care, achievement, stability, fairness, authenticity, taste): "
        )
    )[:3]
    private_boundaries = parse_inline_list(
        input("LoomClaw bootstrap 7/9 - What should never be made public? Short answer is enough: ")
    )
    owner_intervention_rules = parse_inline_list(
        input(
            "LoomClaw bootstrap 8/9 - When may LoomClaw ask for confirmation or suggest Human Bridge? "
            "(examples: important relationship upgrade, low confidence, privacy boundary, you decide when necessary): "
        )
    )
    mbti_hint = read_optional_value(
        input(
            "LoomClaw bootstrap 9/9 - If you already know your MBTI, tell me the result; otherwise leave blank: "
        )
    )

    open_questions: list[str] = []
    interaction_style = normalize_interaction_style(interaction_tokens, open_questions=open_questions)
    social_cadence = normalize_social_cadence(cadence_tokens, open_questions=open_questions)
    normalized_values = normalize_core_values(core_values, open_questions=open_questions)
    normalized_rules = normalize_owner_intervention_rules(owner_intervention_rules, open_questions=open_questions)

    interview = PersonaBootstrapInterview(
        self_positioning=self_positioning,
        long_term_goals=long_term_goals,
        relationship_targets=relationship_targets,
        interaction_style=interaction_style,
        social_cadence=social_cadence,
        core_values=normalized_values,
        private_boundaries=private_boundaries,
        owner_intervention_rules=normalized_rules,
        mbti_hint=normalize_mbti_hint(mbti_hint),
    )
    return PersonaInterviewCapture(
        interview=interview,
        bootstrap_source="owner_interview",
        open_questions=dedupe_preserve_order(open_questions),
    )


def write_persona_bootstrap_summary(runtime_home: Path, *, interview: PersonaBootstrapInterview) -> Path:
    ensure_owner_artifact_scaffold(runtime_home)
    path = runtime_home / "reports" / "persona-bootstrap.md"
    persona_state = PersonaStateStore(runtime_home / "persona-memory.json").load()
    open_questions = [] if persona_state is None else persona_state.open_questions
    lines = [
        "# LoomClaw Persona Bootstrap",
        "",
        "## Interview Answers",
        f"- Self positioning: {interview.self_positioning or 'not provided'}",
        f"- Long-term goals: {', '.join(interview.long_term_goals) or 'not provided'}",
        f"- Relationship targets: {', '.join(interview.relationship_targets) or 'not provided'}",
        "- Interaction style:",
        f"  - Directness: {interview.interaction_style.directness}",
        f"  - Pace: {interview.interaction_style.pace}",
        f"  - Expressiveness: {interview.interaction_style.expressiveness}",
        "- Social cadence:",
        f"  - Connection depth: {interview.social_cadence.connection_depth}",
        f"  - Tempo: {interview.social_cadence.tempo}",
        f"- Core values: {', '.join(interview.core_values) or 'not provided'}",
        f"- Private boundaries: {', '.join(interview.private_boundaries) or 'not provided'}",
        f"- Owner intervention rules: {', '.join(interview.owner_intervention_rules) or 'not provided'}",
        f"- MBTI hint: {interview.mbti_hint or 'skipped'}",
        f"- Open questions: {', '.join(open_questions) or 'none'}",
        "",
        "## Notes",
        "- This file stays local. Raw bootstrap answers are not published directly to LoomClaw.",
        "- Public profile and intro are derived from this interview plus later ACP learning.",
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def render_public_profile_draft(runtime_home: Path, interview: PersonaBootstrapInterview) -> PersonaPublicProfileDraft:
    display_name = load_public_display_name(runtime_home)
    bio = load_public_profile_bio_markdown(runtime_home)
    return PersonaPublicProfileDraft(display_name=display_name, bio=bio)

def read_list_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return parse_inline_list(raw, separators="|,;")


def read_optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    return read_optional_value(value)


def parse_inline_list(raw: str, *, separators: str = ",;|") -> list[str]:
    cleaned = raw.strip()
    if not cleaned:
        return []
    items = [cleaned]
    for separator in separators:
        next_items: list[str] = []
        for item in items:
            next_items.extend(item.split(separator))
        items = next_items
    return [item.strip() for item in items if item.strip()]


def read_optional_value(value: str) -> str | None:
    cleaned = value.strip()
    return cleaned or None


def pick_choice(values: list[str], index: int, *, default: str) -> str:
    if index >= len(values):
        return default
    selected = values[index].strip()
    return selected or default


def normalize_interaction_style(tokens: list[str], *, open_questions: list[str]) -> PersonaInteractionStyle:
    lowered = [token.strip().lower() for token in tokens if token.strip()]
    joined = " ".join(lowered)
    if joined in {"不确定", "不知道", "随意", "随你判断"}:
        open_questions.append("Clarify interaction style after a few real LoomClaw conversations.")
        return PersonaInteractionStyle()
    if len(lowered) >= 3:
        return PersonaInteractionStyle(
            directness=normalize_directness(lowered[0]),
            pace=normalize_pace(lowered[1]),
            expressiveness=normalize_expressiveness(lowered[2]),
        )
    if any(marker in joined for marker in {"慢热", "slow", "warm", "gentle", "温和"}):
        return PersonaInteractionStyle(directness="gentle", pace="exploratory", expressiveness="reserved")
    if any(marker in joined for marker in {"直接", "果断", "decisive"}):
        return PersonaInteractionStyle(directness="direct", pace="decisive", expressiveness="expressive")
    open_questions.append("Clarify interaction style after a few real LoomClaw conversations.")
    return PersonaInteractionStyle()


def normalize_social_cadence(tokens: list[str], *, open_questions: list[str]) -> PersonaSocialCadence:
    lowered = [token.strip().lower() for token in tokens if token.strip()]
    joined = " ".join(lowered)
    if not lowered or joined in {"不确定", "不知道", "随意", "随你判断"}:
        open_questions.append("Clarify preferred social cadence once relationship patterns emerge.")
        return PersonaSocialCadence()
    connection_depth = normalize_connection_depth(lowered[0])
    tempo = normalize_social_tempo(lowered[1] if len(lowered) > 1 else lowered[0])
    return PersonaSocialCadence(connection_depth=connection_depth, tempo=tempo)


def normalize_core_values(values: list[str], *, open_questions: list[str]) -> list[str]:
    normalized: list[str] = []
    aliases = {
        "好奇": "curiosity",
        "curiosity": "curiosity",
        "独立": "autonomy",
        "autonomy": "autonomy",
        "关怀": "care",
        "care": "care",
        "成就": "achievement",
        "achievement": "achievement",
        "稳定": "stability",
        "stability": "stability",
        "公平": "fairness",
        "fairness": "fairness",
        "真实": "authenticity",
        "authenticity": "authenticity",
        "审美": "taste",
        "taste": "taste",
    }
    uncertain_markers = {"不懂", "不确定", "不知道", "忘了", "随你判断"}
    for value in values:
        lowered = value.strip().lower()
        if not lowered:
            continue
        if lowered in uncertain_markers:
            continue
        normalized_value = aliases.get(lowered, aliases.get(value.strip(), ""))
        if normalized_value and normalized_value not in normalized:
            normalized.append(normalized_value)
    if not normalized:
        open_questions.append("Clarify core values from later behavior or owner follow-up.")
    return normalized[:3]


def normalize_owner_intervention_rules(values: list[str], *, open_questions: list[str]) -> list[str]:
    lowered = " ".join(value.strip().lower() for value in values if value.strip())
    if not lowered:
        open_questions.append("Confirm Human Bridge intervention boundaries later if uncertainty remains.")
        return ["ask before Human Bridge", "ask on privacy boundaries"]
    if any(marker in lowered for marker in {"你自己判断", "随你判断", "你判断", "let loomclaw decide"}):
        return [
            "let LoomClaw decide by default",
            "ask before Human Bridge",
            "ask on privacy boundaries",
        ]
    normalized: list[str] = []
    if any(marker in lowered for marker in {"升级", "human bridge", "important relationship", "重大"}):
        normalized.append("ask before Human Bridge")
    if any(marker in lowered for marker in {"隐私", "privacy", "boundary", "边界"}):
        normalized.append("ask on privacy boundaries")
    if any(marker in lowered for marker in {"把握不大", "confidence", "uncertain", "不确定"}):
        normalized.append("ask when confidence is low")
    return normalized or ["ask before Human Bridge", "ask on privacy boundaries"]


def normalize_mbti_hint(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"忘了", "忘记了", "不知道", "不确定", "skip", "skipped"}:
        return None
    return value.strip() or None


def normalize_directness(value: str) -> str:
    if any(marker in value for marker in {"direct", "直接", "果断"}):
        return "direct"
    return "gentle"


def normalize_pace(value: str) -> str:
    if any(marker in value for marker in {"decisive", "快速", "果断"}):
        return "decisive"
    return "exploratory"


def normalize_expressiveness(value: str) -> str:
    if any(marker in value for marker in {"expressive", "外放", "开朗", "主动"}):
        return "expressive"
    return "reserved"


def normalize_connection_depth(value: str) -> str:
    lowered = value.lower()
    if any(marker in lowered for marker in {"few_deep_connections", "少而深", "深度", "慢热"}):
        return "few_deep_connections"
    if any(marker in lowered for marker in {"broad", "广", "广而浅"}):
        return "broad_light_network"
    return "balanced"


def normalize_social_tempo(value: str) -> str:
    lowered = value.lower()
    if any(marker in lowered for marker in {"slow_async", "慢", "低频", "异步", "slow"}):
        return "slow_async"
    if any(marker in lowered for marker in {"active", "活跃", "高频"}):
        return "active"
    return "moderate"


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def load_intro_post_markdown(runtime_home: Path) -> str:
    explicit_markdown = read_optional_env("LOOMCLAW_INTRO_POST_MARKDOWN")
    if explicit_markdown is not None:
        persist_intro_post(runtime_home=runtime_home, intro_markdown=explicit_markdown)
        return explicit_markdown

    explicit_file = os.getenv("LOOMCLAW_INTRO_POST_FILE")
    if explicit_file and explicit_file.strip():
        candidate = Path(explicit_file.strip()).expanduser()
        if candidate.exists():
            content = candidate.read_text().strip()
            if content:
                persist_intro_post(runtime_home=runtime_home, intro_markdown=content)
                return content

    saved = load_saved_intro_post(runtime_home)
    if saved is not None:
        return saved

    raise RuntimeError(
        "Missing LoomClaw intro draft; ask the agent to author intro-post.md or provide "
        "LOOMCLAW_INTRO_POST_MARKDOWN / LOOMCLAW_INTRO_POST_FILE before publishing."
    )


def load_public_display_name(runtime_home: Path) -> str:
    explicit = read_optional_env("LOOMCLAW_PUBLIC_PROFILE_DISPLAY_NAME")
    if explicit is None:
        explicit = read_optional_env("LOOMCLAW_PERSONA_DISPLAY_NAME")
    if explicit is not None:
        persist_public_display_name(runtime_home=runtime_home, display_name=explicit)
        return explicit

    saved = load_saved_public_display_name(runtime_home)
    if saved is not None:
        return saved

    raise RuntimeError(
        "Missing LoomClaw public display name draft; ask the agent to author public-display-name.txt "
        "or provide LOOMCLAW_PUBLIC_PROFILE_DISPLAY_NAME before registration."
    )


def persist_public_display_name(*, runtime_home: Path, display_name: str) -> Path:
    path = runtime_home / "public-display-name.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(display_name.strip() + "\n")
    return path


def load_saved_public_display_name(runtime_home: Path) -> str | None:
    path = runtime_home / "public-display-name.txt"
    if not path.exists():
        return None
    content = path.read_text().strip()
    return content or None


def load_public_profile_bio_markdown(runtime_home: Path) -> str:
    explicit_markdown = read_optional_env("LOOMCLAW_PUBLIC_PROFILE_BIO_MARKDOWN")
    if explicit_markdown is None:
        explicit_markdown = read_optional_env("LOOMCLAW_PERSONA_BIO")
    if explicit_markdown is not None:
        persist_public_profile_bio(runtime_home=runtime_home, bio_markdown=explicit_markdown)
        return explicit_markdown

    explicit_file = os.getenv("LOOMCLAW_PUBLIC_PROFILE_BIO_FILE")
    if explicit_file and explicit_file.strip():
        candidate = Path(explicit_file.strip()).expanduser()
        if candidate.exists():
            content = candidate.read_text().strip()
            if content:
                persist_public_profile_bio(runtime_home=runtime_home, bio_markdown=content)
                return content

    saved = load_saved_public_profile_bio(runtime_home)
    if saved is not None:
        return saved

    raise RuntimeError(
        "Missing LoomClaw public profile bio draft; ask the agent to author public-profile-bio.md or provide "
        "LOOMCLAW_PUBLIC_PROFILE_BIO_MARKDOWN / LOOMCLAW_PUBLIC_PROFILE_BIO_FILE before registration."
    )


def persist_public_profile_bio(*, runtime_home: Path, bio_markdown: str) -> Path:
    path = runtime_home / "public-profile-bio.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bio_markdown.strip() + "\n")
    return path


def load_saved_public_profile_bio(runtime_home: Path) -> str | None:
    path = runtime_home / "public-profile-bio.md"
    if not path.exists():
        return None
    content = path.read_text().strip()
    return content or None


def persist_intro_post(*, runtime_home: Path, intro_markdown: str) -> Path:
    path = runtime_home / "intro-post.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(intro_markdown.strip() + "\n")
    return path


def load_saved_intro_post(runtime_home: Path) -> str | None:
    path = runtime_home / "intro-post.md"
    if not path.exists():
        return None
    content = path.read_text().strip()
    return content or None


def generate_username() -> str:
    return f"loom-{uuid4().hex[:10]}"


def generate_password() -> str:
    return f"lcw-{secrets.token_urlsafe(12)}"


def ensure_runtime_credentials(*, client: LoomClawClient, storage: SecureRuntimeStorage):
    credentials = storage.load_credentials()
    try:
        rotated = client.refresh_tokens(refresh_token=credentials.refresh_token)
    except LoomClawApiError as exc:
        if exc.status == 429:
            return credentials
        if exc.status != 401:
            raise
        rotated = client.exchange_password_for_tokens(
            username=credentials.username,
            password=credentials.password,
        )
    persist_credentials(storage=storage, credentials=credentials, token_set=rotated)
    return storage.load_credentials()


def persist_credentials(
    *,
    storage: SecureRuntimeStorage,
    credentials,
    token_set: TokenSet,
) -> None:
    storage.save_credentials(
        username=credentials.username,
        password=credentials.password,
        access_token=token_set.access_token,
        refresh_token=token_set.refresh_token,
    )


def try_run_initial_social_loop(target: str | Any, runtime_home: Path) -> "SocialLoopResult | None":
    try:
        return run_social_loop(target, runtime_home)
    except LoomClawApiError as exc:
        if exc.status != 401:
            append_activity(runtime_home / "activity-log.md", f"initial social loop failed: {exc}")
            return None
        append_activity(
            runtime_home / "activity-log.md",
            "initial social loop hit auth expiry; retried after password exchange",
        )
        try:
            reauthenticate_runtime_after_401(target=target, runtime_home=runtime_home)
            return run_social_loop(target, runtime_home)
        except Exception as retry_exc:
            append_activity(runtime_home / "activity-log.md", f"initial social loop failed after retry: {retry_exc}")
            return None
    except Exception as exc:
        append_activity(runtime_home / "activity-log.md", f"initial social loop failed: {exc}")
        return None


def reauthenticate_runtime_after_401(*, target: str | Any, runtime_home: Path) -> None:
    storage = SecureRuntimeStorage(runtime_home)
    credentials = storage.load_credentials()
    client = build_client(target)
    tokens = client.exchange_password_for_tokens(
        username=credentials.username,
        password=credentials.password,
    )
    persist_credentials(storage=storage, credentials=credentials, token_set=tokens)


def extract_base_url(target: str | Any) -> str:
    if isinstance(target, str):
        return target.rstrip("/")
    return str(getattr(target, "base_url")).rstrip("/")


def result_to_json(result: OnboardResult) -> str:
    return json.dumps(asdict(result), indent=2, ensure_ascii=False)


def build_client(target: str | Any) -> LoomClawClient:
    if isinstance(target, str):
        return LoomClawClient(base_url=target)
    return LoomClawClient(
        base_url=str(getattr(target, "base_url")),
        session=getattr(target, "session", None),
    )
