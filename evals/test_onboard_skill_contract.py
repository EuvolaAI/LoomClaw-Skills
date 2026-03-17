from pathlib import Path


def test_onboard_skill_references_owner_dialogue_contract() -> None:
    skill_path = Path(__file__).resolve().parents[1] / "loomclaw-onboard" / "SKILL.md"
    content = skill_path.read_text()

    assert "references/owner-dialogue.md" in content
    assert "references/persona-interview.md" in content
    assert "references/intro-writing.md" in content
    assert "Do not create probe accounts" in content
    assert "Do not present shell commands" in content


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
    assert "formulaic intro posts assembled from slot labels" in content
    assert "probe accounts" in content
