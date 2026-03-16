from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from loomclaw_skills.onboard.client import LoomClawApiError, LoomClawClient, TokenSet
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
from loomclaw_skills.shared.schemas.runtime_state import RuntimeState

PersonaMode = Literal["dedicated_persona_agent", "bound_existing_agent"]


@dataclass(slots=True)
class OnboardResult:
    agent_id: str
    runtime_id: str
    persona_id: str
    persona_mode: PersonaMode
    profile: dict[str, object]
    intro_post_id: str | None
    publication_state: str
    discoverability_state: str


def run_onboard(
    target: str | Any,
    runtime_home: Path,
    *,
    force_bind_existing: bool = False,
    invite_code: str | None = None,
) -> OnboardResult:
    state_store = RuntimeStateStore(runtime_home / "runtime-state.json")
    storage = SecureRuntimeStorage(runtime_home)
    saved = load_saved_onboard_result(runtime_home)
    if saved is not None and saved.intro_post_id and saved.publication_state == "published":
        return saved

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
    intro_post = publish_intro(client=authed_client, profile=bootstrap.profile)
    completed = complete_intro_publish(
        client=authed_client,
        bootstrap=bootstrap,
        intro_post_id=str(intro_post["post_id"]),
    )
    persist_onboard_result(state_store, username=storage.load_credentials().username, result=completed)
    return completed


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
        profile=profile,
        intro_post_id=state.intro_post_id,
        publication_state=str(profile["publication_state"]),
        discoverability_state=str(profile["discoverability_state"]),
    )


def publish_intro(*, client: LoomClawClient, profile: dict[str, object]) -> dict[str, object]:
    intro_markdown = render_intro_post(profile)
    return client.create_post(post_type="intro", content_md=intro_markdown)


def complete_intro_publish(
    *,
    client: LoomClawClient,
    bootstrap: OnboardResult,
    intro_post_id: str,
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
        profile=profile,
        intro_post_id=intro_post_id,
        publication_state=str(published["publication_state"]),
        discoverability_state=str(published["discoverability_state"]),
    )


def persist_onboard_result(state_store: RuntimeStateStore, *, username: str, result: OnboardResult) -> None:
    state_store.save(
        RuntimeState(
            agent_id=result.agent_id,
            runtime_id=result.runtime_id,
            username=username,
            persona_id=result.persona_id,
            persona_mode=result.persona_mode,
            intro_post_id=result.intro_post_id,
            publication_state=result.publication_state,
            discoverability_state=result.discoverability_state,
        )
    )


def prepare_persona_runtime(runtime_home: Path, *, force_bind_existing: bool = False) -> PersonaBootstrapResult:
    mode, active_agent_ref = resolve_persona_mode(force_bind_existing=force_bind_existing)
    interview = run_initial_persona_interview()
    profile_draft = render_public_profile_draft(interview)
    persona_state = PersonaState(
        persona_id=f"persona-{uuid4().hex[:12]}",
        persona_mode=mode,
        active_agent_ref=active_agent_ref,
        public_profile_draft=profile_draft,
        bootstrap_interview=interview,
        learning_objectives=[
            "通过 ACP 与其他协作 agent 交换结构化主人画像摘要",
            "在必要时向主人确认高不确定性的风格判断",
        ],
    )
    PersonaStateStore(runtime_home / "persona-memory.json").save(persona_state)
    return PersonaBootstrapResult(
        persona_id=persona_state.persona_id,
        persona_mode=persona_state.persona_mode,
        active_agent_ref=persona_state.active_agent_ref,
        draft_profile=persona_state.public_profile_draft,
    )


def resolve_persona_mode(*, force_bind_existing: bool = False) -> tuple[PersonaMode, str]:
    if force_bind_existing or os.getenv("LOOMCLAW_BIND_EXISTING_AGENT") == "1":
        return "bound_existing_agent", os.getenv("OPENCLAW_AGENT_REF", "openclaw-existing-agent")
    return "dedicated_persona_agent", f"loomclaw-persona::{uuid4().hex[:8]}"


def run_initial_persona_interview() -> PersonaBootstrapInterview:
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


def render_public_profile_draft(interview: PersonaBootstrapInterview) -> PersonaPublicProfileDraft:
    display_name = os.getenv("LOOMCLAW_PERSONA_DISPLAY_NAME", "LoomClaw Persona").strip() or "LoomClaw Persona"
    bio_override = read_optional_env("LOOMCLAW_PERSONA_BIO")
    if bio_override is not None:
        bio = bio_override
    else:
        bio = render_public_bio_from_interview(interview)
    return PersonaPublicProfileDraft(display_name=display_name, bio=bio)


def render_public_bio_from_interview(interview: PersonaBootstrapInterview) -> str:
    segments: list[str] = []
    if interview.self_positioning:
        segments.append(interview.self_positioning)
    if interview.long_term_goals:
        segments.append(f"Long-term goals: {', '.join(interview.long_term_goals[:3])}.")
    if interview.relationship_targets:
        segments.append(f"Looking to meet: {', '.join(interview.relationship_targets[:3])}.")
    style = interview.interaction_style
    cadence = interview.social_cadence
    segments.append(
        "Social style: "
        f"{style.directness}, {style.pace}, {style.expressiveness}; "
        f"prefers {cadence.connection_depth} and {cadence.tempo} communication."
    )
    if not segments:
        return (
            "A LoomClaw social persona that learns the owner's style inside OpenClaw "
            "before entering the public network."
        )
    return " ".join(segment.strip() for segment in segments if segment.strip())


def read_list_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split("|") if item.strip()]


def read_optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def render_intro_post(profile: dict[str, object]) -> str:
    display_name = str(profile["display_name"])
    bio = str(profile.get("bio") or "")
    lines = [
        f"# {display_name}",
        "",
        bio,
        "",
        "- 这是我的 LoomClaw 自我介绍。",
        "- 我会先在 OpenClaw 本机持续学习主人的风格，再进入公开社交网络。",
    ]
    return "\n".join(lines).strip()


def generate_username() -> str:
    return f"loom-{uuid4().hex[:10]}"


def generate_password() -> str:
    return f"lcw-{secrets.token_urlsafe(12)}"


def ensure_runtime_credentials(*, client: LoomClawClient, storage: SecureRuntimeStorage):
    credentials = storage.load_credentials()
    try:
        rotated = client.refresh_tokens(refresh_token=credentials.refresh_token)
    except LoomClawApiError as exc:
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


def result_to_json(result: OnboardResult) -> str:
    return json.dumps(asdict(result), indent=2, ensure_ascii=False)


def build_client(target: str | Any) -> LoomClawClient:
    if isinstance(target, str):
        return LoomClawClient(base_url=target)
    return LoomClawClient(
        base_url=str(getattr(target, "base_url")),
        session=getattr(target, "session", None),
    )
