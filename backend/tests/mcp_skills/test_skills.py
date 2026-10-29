from pathlib import Path

import pytest

from app.assistant import skills


def test_discovery_is_metadata_only_and_load_is_explicit() -> None:
    available = skills.list_skills()
    assert {item["name"] for item in available} == {
        "document-qa",
        "source-comparison",
        "research-planning",
    }
    assert all("instructions" not in item for item in available)
    for item in available:
        loaded = skills.load_skill(item["name"])
        assert loaded["version"] == "1.0.0"
        assert loaded["instructions"]
        assert set(loaded["allowed_tools"]) <= {
            "ask_knowledge",
            "research_topic",
            "search_web",
        }


@pytest.mark.parametrize(
    "name", ["../document-qa", "/etc/passwd", "unknown", "", "document-qa/SKILL.md"]
)
def test_only_reviewed_exact_names_are_loadable(name: str) -> None:
    with pytest.raises(skills.SkillError, match="Unknown reviewed skill"):
        skills.load_skill(name)


def test_symlinked_skill_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = skills._SKILLS_ROOT
    root = tmp_path / "skills"
    root.mkdir()
    (root / "document-qa").symlink_to(
        original / "document-qa", target_is_directory=True
    )
    monkeypatch.setattr(skills, "_SKILLS_ROOT", root)
    with pytest.raises(skills.SkillError, match="unsafe"):
        skills.load_skill("document-qa")


def test_symlinked_file_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = skills._SKILLS_ROOT
    folder = tmp_path / "document-qa"
    folder.mkdir()
    (folder / "SKILL.md").symlink_to(original / "document-qa" / "SKILL.md")
    monkeypatch.setattr(skills, "_SKILLS_ROOT", tmp_path)
    with pytest.raises(skills.SkillError, match="unsafe"):
        skills.load_skill("document-qa")


@pytest.mark.parametrize(
    "text",
    [
        "plain instructions without metadata",
        "---\nname: document-qa\ndescription: test\nmetadata: {version: '1'}\nallowed-tools: execute_code\n---\nRun code",
        "---\nname: source-comparison\n---\nwrong name",
        "---\n!!python/object/apply:os.system ['touch should-not-exist']\n---\nunsafe yaml",
        "x" * 32_769,
    ],
)
def test_invalid_or_executable_metadata_is_rejected(
    text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "document-qa"
    folder.mkdir()
    (folder / "SKILL.md").write_text(text)
    monkeypatch.setattr(skills, "_SKILLS_ROOT", tmp_path)
    with pytest.raises(skills.SkillError):
        skills.load_skill("document-qa")
    assert not (tmp_path / "should-not-exist").exists()
