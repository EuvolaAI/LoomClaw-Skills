from pathlib import Path


def test_onboard_skill_references_owner_dialogue_contract() -> None:
    skill_path = Path(__file__).resolve().parents[1] / "loomclaw-onboard" / "SKILL.md"
    content = skill_path.read_text()

    assert "references/owner-dialogue.md" in content
    assert "references/persona-interview.md" in content
    assert "references/profile-writing.md" in content
    assert "references/intro-writing.md" in content
    assert "Do not create probe accounts" in content
    assert "Do not present shell commands" in content
    assert "Prepare sibling skills from the same checked-out LoomClaw skills repository" in content
    assert "Base URL priority" in content


def test_owner_dialogue_contract_bans_engineering_default_output() -> None:
    reference_path = (
        Path(__file__).resolve().parents[1]
        / "loomclaw-onboard"
        / "references"
        / "owner-dialogue.md"
    )
    content = reference_path.read_text()

    assert "Do not patch vendored LoomClaw skill code" in content
    assert '"I’ll run a backend verification first"' in content
    assert '"I patched the repo locally"' in content
    assert '"There are two ways to provide your persona info"' in content
    assert '"I prepared this runtime directory for you"' in content
    assert "formulaic public profile bios assembled from questionnaire slots" in content
    assert "formulaic intro posts assembled from slot labels" in content
    assert "probe accounts" in content


def test_social_loop_contract_requires_agent_authored_public_sync_drafts() -> None:
    skill_path = Path(__file__).resolve().parents[1] / "loomclaw-social-loop" / "SKILL.md"
    reference_path = (
        Path(__file__).resolve().parents[1]
        / "loomclaw-social-loop"
        / "references"
        / "social-loop.md"
    )

    skill_content = skill_path.read_text()
    reference_content = reference_path.read_text()

    assert "references/public-sync-writing.md" in skill_content
    assert "defer the public sync" in skill_content
    assert "runtime_home/public-sync/request.md" in skill_content
    assert "public-sync/profile-bio.md" in reference_content
    assert "never be assembled from trait labels" in reference_content


def test_owner_report_contract_defines_narrative_shape() -> None:
    skill_path = Path(__file__).resolve().parents[1] / "loomclaw-owner-report" / "SKILL.md"
    reference_path = (
        Path(__file__).resolve().parents[1]
        / "loomclaw-owner-report"
        / "references"
        / "reporting.md"
    )

    skill_content = skill_path.read_text()
    reference_content = reference_path.read_text()

    assert "Owner-Facing Summary Shape" in skill_content
    assert "calm, concrete, and relationship-aware" in skill_content
    assert "state meaningful progress first" in reference_content


def test_human_bridge_contract_defines_non_action_boundary() -> None:
    skill_path = Path(__file__).resolve().parents[1] / "loomclaw-human-bridge" / "SKILL.md"
    reference_path = (
        Path(__file__).resolve().parents[1]
        / "loomclaw-human-bridge"
        / "references"
        / "bridge-flow.md"
    )

    skill_content = skill_path.read_text()
    reference_content = reference_path.read_text()

    assert "Owner-Facing Recommendation Shape" in skill_content
    assert "explicitly say that no invitation has been sent" in skill_content
    assert "not enough to leak identifiable real-world details" in reference_content
