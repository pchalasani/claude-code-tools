"""Generate OpenCode agent markdown from Claude Code agents."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..models import (
    ClaudeCodeAgent,
    OpenCodeAgent,
    MODEL_MAPPING,
    PERMISSION_MODE_MAPPING,
)


def get_template_env() -> Environment:
    """Get Jinja2 environment with templates directory."""
    templates_dir = Path(__file__).parent.parent / "templates"
    return Environment(
        loader=FileSystemLoader(templates_dir),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def translate_model(claude_model: str | None) -> str | None:
    """
    Translate Claude Code model name to OpenCode format.

    Args:
        claude_model: Claude Code model name (sonnet, opus, haiku).

    Returns:
        OpenCode model string (provider/model-id) or None.
    """
    if not claude_model:
        return None

    # Check if it's already a full model ID
    if "/" in claude_model:
        return claude_model

    # Translate short names
    return MODEL_MAPPING.get(claude_model.lower())


def translate_permission_mode(mode: str | None) -> dict | None:
    """
    Translate Claude Code permission mode to OpenCode permission config.

    Args:
        mode: Claude Code permission mode.

    Returns:
        OpenCode permission dict or None.
    """
    if not mode:
        return None

    return PERMISSION_MODE_MAPPING.get(mode)


def convert_agent(agent: ClaudeCodeAgent) -> OpenCodeAgent:
    """
    Convert Claude Code agent to OpenCode agent format.

    Args:
        agent: ClaudeCodeAgent object.

    Returns:
        OpenCodeAgent object.
    """
    permission = translate_permission_mode(agent.permission_mode)

    # If skills are specified, add note to prompt
    prompt = agent.prompt
    if agent.skills:
        skills_note = (
            f"\n\n<!-- Note: This agent originally used skills: "
            f"{', '.join(agent.skills)}. "
            f"Skills are loaded automatically in OpenCode if available. -->\n"
        )
        prompt = prompt + skills_note

    return OpenCodeAgent(
        name=agent.name,
        description=agent.description,
        mode="subagent",
        model=translate_model(agent.model),
        permission=permission,
        prompt=prompt,
    )


def generate_agent(agent: ClaudeCodeAgent) -> str:
    """
    Generate OpenCode agent markdown from Claude Code agent.

    Args:
        agent: ClaudeCodeAgent object.

    Returns:
        OpenCode agent markdown content.
    """
    env = get_template_env()
    template = env.get_template("agent.md.j2")

    opencode_agent = convert_agent(agent)

    return template.render(agent=opencode_agent)


def write_agent(agent: ClaudeCodeAgent, output_dir: Path) -> Path:
    """
    Generate and write OpenCode agent file.

    Args:
        agent: ClaudeCodeAgent object.
        output_dir: Directory to write agent to (.opencode/agent/).

    Returns:
        Path to the generated agent file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{agent.name}.md"

    agent_md = generate_agent(agent)
    output_path.write_text(agent_md)

    return output_path
