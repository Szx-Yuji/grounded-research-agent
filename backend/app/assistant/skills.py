"""Discover and explicitly load a fixed set of reviewed, read-only workflows."""

from pathlib import Path
from typing import Any

import yaml

_SKILLS_ROOT = Path(__file__).resolve().parents[3] / "skills"
_REVIEWED_SKILLS = (
    "document-qa",
    "research-planning",
    "source-comparison",
)
_ALLOWED_TOOLS = frozenset({"ask_knowledge", "research_topic", "search_web"})
_MAX_SKILL_BYTES = 32_768


class SkillError(ValueError):
    """An unknown skill or an invalid local workflow; safe to show to the model."""


def _read_skill(name: str) -> dict[str, Any]:
    if name not in _REVIEWED_SKILLS:
        raise SkillError(
            "Unknown reviewed skill. Call list_skills for available names."
        )
    directory = _SKILLS_ROOT / name
    path = directory / "SKILL.md"
    if (
        _SKILLS_ROOT.is_symlink()
        or directory.is_symlink()
        or path.is_symlink()
        or not path.is_file()
        or path.resolve().parent != directory.resolve()
        or directory.resolve().parent != _SKILLS_ROOT.resolve()
    ):
        raise SkillError("Reviewed skill file is unavailable or unsafe.")
    try:
        # Read a bounded file rather than accepting model-supplied paths/resources.
        with path.open("rb") as file:
            raw = file.read(_MAX_SKILL_BYTES + 1)
        if len(raw) > _MAX_SKILL_BYTES:
            raise SkillError("Reviewed skill file exceeds the size limit.")
        text = raw.decode("utf-8")
        sections = text.split("---", 2)
        if len(sections) != 3 or sections[0].strip():
            raise SkillError("Reviewed skill metadata is invalid.")
        metadata = yaml.safe_load(sections[1])
    except OSError, UnicodeError, yaml.YAMLError:
        raise SkillError("Reviewed skill file could not be read.") from None
    if not isinstance(metadata, dict) or metadata.get("name") != name:
        raise SkillError("Reviewed skill metadata is invalid.")
    description = metadata.get("description")
    extension = metadata.get("metadata", {})
    version = extension.get("version") if isinstance(extension, dict) else None
    tool_string = metadata.get("allowed-tools")
    if (
        not isinstance(description, str)
        or not 1 <= len(description.strip()) <= 1024
        or not isinstance(version, str)
        or not version.strip()
        or not isinstance(tool_string, str)
    ):
        raise SkillError("Reviewed skill metadata is invalid.")
    tools = tool_string.split()
    instructions = sections[2].strip()
    if not tools or not set(tools) <= _ALLOWED_TOOLS or not instructions:
        raise SkillError("Reviewed skill tools or instructions are invalid.")
    return {
        "name": name,
        "description": description.strip(),
        "version": version,
        "allowed_tools": tools,
        "instructions": instructions,
    }


def list_skills() -> list[dict[str, Any]]:
    """Return discovery metadata only, without adding all instructions to context."""
    return [
        {
            key: value
            for key, value in _read_skill(name).items()
            if key != "instructions"
        }
        for name in _REVIEWED_SKILLS
    ]


def load_skill(name: str) -> dict[str, Any]:
    """Load one reviewed workflow by exact name; never execute embedded code."""
    return _read_skill(name)
