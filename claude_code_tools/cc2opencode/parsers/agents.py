"""Parse Claude Code agents from .claude/agents/*.md."""

import re
from pathlib import Path

import yaml

from ..models import ClaudeCodeAgent


def parse_agent(path: Path) -> ClaudeCodeAgent:
    """
    Parse a single Claude Code agent file.

    Args:
        path: Path to the agent markdown file.

    Returns:
        ClaudeCodeAgent object.
    """
    content = path.read_text()

    # Extract frontmatter manually to handle edge cases
    # Some Claude Code agents have complex descriptions with \n characters
    if content.startswith("---"):
        # Find the closing ---
        end_match = re.search(r"\n---\s*\n", content[3:])
        if end_match:
            frontmatter_str = content[3 : 3 + end_match.start()]
            body = content[3 + end_match.end() :]

            # Try to parse the frontmatter
            try:
                metadata = yaml.safe_load(frontmatter_str)
            except yaml.YAMLError:
                # If YAML parsing fails, try to extract key fields manually
                metadata = {}
                # Extract name
                name_match = re.search(r"^name:\s*(.+)$", frontmatter_str, re.M)
                if name_match:
                    metadata["name"] = name_match.group(1).strip()
                # Extract description (may be multi-line or have escaped chars)
                desc_match = re.search(
                    r"^description:\s*(.+?)(?=\n[a-z_]+:|$)",
                    frontmatter_str,
                    re.M | re.S,
                )
                if desc_match:
                    desc = desc_match.group(1).strip()
                    # Truncate very long descriptions
                    if len(desc) > 500:
                        desc = desc[:500] + "..."
                    metadata["description"] = desc
                # Extract other simple fields
                for field in ["model", "tools", "skills", "permissionMode", "color"]:
                    match = re.search(rf"^{field}:\s*(.+)$", frontmatter_str, re.M)
                    if match:
                        metadata[field] = match.group(1).strip()
        else:
            metadata = {}
            body = content
    else:
        metadata = {}
        body = content

    # Get name from frontmatter or filename
    name = metadata.get("name", path.stem)

    # Parse tools (comma-separated string to list)
    tools_str = metadata.get("tools", "")
    tools = [t.strip() for t in tools_str.split(",")] if tools_str else None

    # Parse skills (comma-separated string to list)
    skills_str = metadata.get("skills", "")
    skills = [s.strip() for s in skills_str.split(",")] if skills_str else None

    return ClaudeCodeAgent(
        name=name,
        description=metadata.get("description", ""),
        tools=tools,
        model=metadata.get("model"),
        permission_mode=metadata.get("permissionMode"),
        skills=skills,
        prompt=body.strip(),
    )


def parse_agents(agents_dir: Path) -> list[ClaudeCodeAgent]:
    """
    Parse all agent files in a directory.

    Args:
        agents_dir: Path to .claude/agents/ directory.

    Returns:
        List of ClaudeCodeAgent objects.
    """
    agents = []

    if not agents_dir.exists():
        return agents

    for agent_file in agents_dir.glob("*.md"):
        try:
            agent = parse_agent(agent_file)
            agents.append(agent)
        except Exception as e:
            # Log warning but continue
            print(f"Warning: Failed to parse agent {agent_file}: {e}")

    return agents
