"""Parse Claude Code skills from .claude/skills/."""

from pathlib import Path

import frontmatter

from ..models import ClaudeCodeSkill


def parse_skill(skill_dir: Path) -> ClaudeCodeSkill | None:
    """
    Parse a single Claude Code skill directory.

    Args:
        skill_dir: Path to the skill directory (containing SKILL.md).

    Returns:
        ClaudeCodeSkill object or None if invalid.
    """
    skill_file = skill_dir / "SKILL.md"

    if not skill_file.exists():
        return None

    try:
        post = frontmatter.load(skill_file)
        content = skill_file.read_text()

        return ClaudeCodeSkill(
            name=post.get("name", skill_dir.name),
            description=post.get("description", ""),
            path=skill_dir,
            content=content,
        )
    except Exception:
        return None


def parse_skills(skills_dir: Path) -> list[ClaudeCodeSkill]:
    """
    Parse all skills in a directory.

    Args:
        skills_dir: Path to .claude/skills/ directory.

    Returns:
        List of ClaudeCodeSkill objects.
    """
    skills = []

    if not skills_dir.exists():
        return skills

    # Each subdirectory is a skill
    for skill_subdir in skills_dir.iterdir():
        if skill_subdir.is_dir():
            skill = parse_skill(skill_subdir)
            if skill:
                skills.append(skill)

    return skills
