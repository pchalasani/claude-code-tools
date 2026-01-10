"""CLI for cc2opencode - Migrate Claude Code to OpenCode."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from .detect import detect_claude_code, summarize_config
from .generators.plugin import write_plugin
from .generators.agent import write_agent
from .generators.command import write_command
from .generators.config import write_opencode_config

app = typer.Typer(
    name="cc2opencode",
    help="Migrate Claude Code configurations to OpenCode format.",
    add_completion=False,
)
console = Console()


def print_summary(summary: dict) -> None:
    """Print a summary table of detected configurations."""
    table = Table(title="Detected Claude Code Configuration")

    table.add_column("Component", style="cyan")
    table.add_column("Count", style="magenta")
    table.add_column("Details", style="green")

    table.add_row(
        "Hooks",
        str(summary["hooks"]["count"]),
        ", ".join(summary["hooks"]["events"]) if summary["hooks"]["events"] else "-",
    )
    table.add_row(
        "Agents",
        str(summary["agents"]["count"]),
        ", ".join(summary["agents"]["names"]) if summary["agents"]["names"] else "-",
    )
    table.add_row(
        "Commands",
        str(summary["commands"]["count"]),
        ", ".join(summary["commands"]["names"][:5])
        + ("..." if len(summary["commands"]["names"]) > 5 else "")
        if summary["commands"]["names"]
        else "-",
    )
    table.add_row(
        "Skills",
        str(summary["skills"]["count"]),
        ", ".join(summary["skills"]["names"]) if summary["skills"]["names"] else "-",
    )
    table.add_row(
        "MCP Servers",
        str(summary["mcp_servers"]["count"]),
        ", ".join(summary["mcp_servers"]["names"])
        if summary["mcp_servers"]["names"]
        else "-",
    )
    table.add_row(
        "CLAUDE.md",
        "Yes" if summary["has_claude_md"] else "No",
        "-",
    )

    console.print(table)


def print_migration_plan(summary: dict, output_dir: Path) -> None:
    """Print what files will be created."""
    tree = Tree(f"[bold]{output_dir}[/bold]")

    if summary["hooks"]["count"] > 0:
        plugin_branch = tree.add("[cyan]plugin/[/cyan]")
        plugin_branch.add("migrated_hooks.ts")

    if summary["agents"]["count"] > 0:
        agent_branch = tree.add("[cyan]agent/[/cyan]")
        for name in summary["agents"]["names"]:
            agent_branch.add(f"{name}.md")

    if summary["commands"]["count"] > 0:
        command_branch = tree.add("[cyan]command/[/cyan]")
        for name in summary["commands"]["names"]:
            # Handle namespaced commands
            display_name = name.replace(":", "/") + ".md"
            command_branch.add(display_name)

    if summary["mcp_servers"]["count"] > 0:
        tree.add("opencode.json")

    if summary["skills"]["count"] > 0:
        skill_note = tree.add("[dim]skill/[/dim]")
        skill_note.add(
            "[dim](Skills are compatible - no migration needed)[/dim]"
        )

    console.print("\n[bold]Files to be created:[/bold]")
    console.print(tree)


@app.command()
def migrate(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the project directory with Claude Code config",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory (default: .opencode/ in project)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-d",
        help="Show what would be created without writing files",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show verbose output",
    ),
    skip_hooks: bool = typer.Option(
        False,
        "--skip-hooks",
        help="Skip hook migration",
    ),
    skip_agents: bool = typer.Option(
        False,
        "--skip-agents",
        help="Skip agent migration",
    ),
    skip_commands: bool = typer.Option(
        False,
        "--skip-commands",
        help="Skip command migration",
    ),
    skip_mcp: bool = typer.Option(
        False,
        "--skip-mcp",
        help="Skip MCP configuration migration",
    ),
) -> None:
    """
    Migrate Claude Code configuration to OpenCode format.

    This tool converts:

    - Hooks -> TypeScript plugins (.opencode/plugin/)

    - Agents -> Agent markdown files (.opencode/agent/)

    - Commands -> Command markdown files (.opencode/command/)

    - MCP config -> opencode.json

    - Skills are already compatible (no migration needed)
    """
    console.print(
        Panel(
            "[bold blue]cc2opencode[/bold blue] - Claude Code to OpenCode Migration",
            subtitle="v0.1.0",
        )
    )

    # Set output directory
    output_dir = output or (path / ".opencode")

    # Detect Claude Code configuration
    console.print(f"\n[bold]Scanning:[/bold] {path}")
    config = detect_claude_code(path)
    summary = summarize_config(config)

    # Print summary
    print_summary(summary)

    # Check if there's anything to migrate
    total = (
        (0 if skip_hooks else summary["hooks"]["count"])
        + (0 if skip_agents else summary["agents"]["count"])
        + (0 if skip_commands else summary["commands"]["count"])
        + (0 if skip_mcp else summary["mcp_servers"]["count"])
    )

    if total == 0:
        console.print(
            "\n[yellow]No Claude Code configuration found to migrate.[/yellow]"
        )
        if summary["skills"]["count"] > 0:
            console.print(
                f"[green]Note: {summary['skills']['count']} skill(s) found - "
                "these are already compatible with OpenCode![/green]"
            )
        raise typer.Exit(0)

    # Print migration plan
    print_migration_plan(summary, output_dir)

    if dry_run:
        console.print("\n[yellow]Dry run - no files written.[/yellow]")
        raise typer.Exit(0)

    # Confirm before proceeding
    if not typer.confirm("\nProceed with migration?"):
        console.print("[yellow]Migration cancelled.[/yellow]")
        raise typer.Exit(0)

    # Perform migration
    console.print("\n[bold]Migrating...[/bold]")
    files_created = []

    # Migrate hooks
    if not skip_hooks and config.hooks:
        plugin_dir = output_dir / "plugin"
        plugin_path = write_plugin(config.hooks, plugin_dir)
        files_created.append(plugin_path)
        console.print(f"  [green]✓[/green] Created {plugin_path}")

    # Migrate agents
    if not skip_agents and config.agents:
        agent_dir = output_dir / "agent"
        for agent in config.agents:
            agent_path = write_agent(agent, agent_dir)
            files_created.append(agent_path)
            console.print(f"  [green]✓[/green] Created {agent_path}")

    # Migrate commands
    if not skip_commands and config.commands:
        command_dir = output_dir / "command"
        for command in config.commands:
            command_path = write_command(command, command_dir)
            files_created.append(command_path)
            console.print(f"  [green]✓[/green] Created {command_path}")

    # Migrate MCP config
    if not skip_mcp and config.mcp_servers:
        config_path = write_opencode_config(config, output_dir)
        files_created.append(config_path)
        console.print(f"  [green]✓[/green] Created {config_path}")

    # Summary
    console.print(
        f"\n[bold green]Migration complete![/bold green] "
        f"Created {len(files_created)} file(s)."
    )

    # Notes
    if config.hooks:
        has_prompt_hooks = any(h.hook_type == "prompt" for h in config.hooks)
        if has_prompt_hooks:
            console.print(
                "\n[yellow]Note:[/yellow] Some hooks used prompts (LLM-based "
                "decisions). These require manual implementation in the generated "
                "TypeScript plugin."
            )

    if summary["skills"]["count"] > 0:
        console.print(
            f"\n[blue]Info:[/blue] {summary['skills']['count']} skill(s) "
            "detected. Skills in .claude/skills/ are already compatible with "
            "OpenCode - no migration needed!"
        )


@app.command()
def detect(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the project directory",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
) -> None:
    """Detect Claude Code configuration without migrating."""
    console.print(f"[bold]Scanning:[/bold] {path}")

    config = detect_claude_code(path)
    summary = summarize_config(config)

    print_summary(summary)

    if summary["skills"]["count"] > 0:
        console.print(
            f"\n[green]Skills are already compatible with OpenCode![/green]"
        )


def main() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
