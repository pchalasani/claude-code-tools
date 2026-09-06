"""amux -- see and jump between the coding agents running in your tmux panes.

    amux              # interactive picker (fzf): jump to an agent
    amux list         # table of every live agent
    amux list --json  # machine-readable
    amux scan         # refresh the cache (for cron / tmux hooks)

The picker opens on cached rows and refreshes in the background, so it is
instant even with hundreds of panes.
"""

from __future__ import annotations

import argparse
import math
import os
import shlex
import shutil
import subprocess
import sys

from . import cache, render, scan
from .filters import directory_argument, select_agents
from .model import Agent

_FZF_HEADER = (
    "enter=jump  ctrl-r=refresh  esc=quit    "
    "input=asking you  busy=working  bg=background  age=last submitted input"
)


def _fresh(write_cache: bool = True) -> list[Agent]:
    """Scan live panes and refresh the cache."""
    agents = scan.scan()
    if write_cache:
        cache.write(agents)
    return agents


def _agents_for_display(max_age: float) -> tuple[list[Agent], bool]:
    """Return agents to show now, plus whether they came from cache."""
    cached, age = cache.read(max_age=max_age)
    if cached:
        return cached, True
    return _fresh(), False


def _display(agents: list[Agent], args: argparse.Namespace) -> list[Agent]:
    """Refresh ages even for cached rows, then apply the requested view."""
    agents = select_agents(
        agents, getattr(args, "repo", None), getattr(args, "directory", None),
    )
    for agent in agents:
        agent.classify_inactivity(args.dormant_hours)
    if args.dormant:
        agents = [a for a in agents if a.inactivity == "dormant"]
    if args.sort == "oldest":
        agents = sorted(agents, key=lambda a: (
            a.last_input_at is None,
            a.last_input_at if a.last_input_at is not None else 0,
            a.pane,
        ))
    return agents


def cmd_list(args: argparse.Namespace) -> int:
    """Print every live agent as a table or JSON."""
    agents = cache.read(max_age=args.max_age)[0] if args.cached else _fresh()
    if not agents and args.cached:
        agents = _fresh()
    agents = _display(agents, args)
    if args.json:
        print(render.as_json(agents))
    else:
        colour = sys.stdout.isatty() and not args.no_color
        print(render.table(agents, colour=colour))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Refresh the cache and report what was found."""
    agents = _fresh()
    print(f"cached {len(agents)} agents -> {cache.cache_path()}")
    return 0


def cmd_rows(args: argparse.Namespace) -> int:
    """Emit picker rows (internal: used by fzf's reload binding)."""
    agents = _fresh() if args.refresh else _agents_for_display(args.max_age)[0]
    print(render.picker_lines(_display(agents, args), colour=True))
    return 0


def cmd_pick(args: argparse.Namespace) -> int:
    """Interactive picker; jumps to the chosen agent's pane."""
    if not shutil.which("fzf"):
        print("amux: fzf not found (brew install fzf)", file=sys.stderr)
        return 1
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("amux: no tty -- use 'amux list' for scripted output", file=sys.stderr)
        return 1

    agents, from_cache = _agents_for_display(args.max_age)
    agents = _display(agents, args)
    if not agents and from_cache:
        agents = _display(_fresh(), args)
        from_cache = False
    if not agents:
        print("no matching agents")
        return 0

    # fzf runs bind commands through a shell, so the interpreter path must be
    # quoted: a venv at "/tmp/my venv/bin/python" would otherwise run "/tmp/my".
    self_cmd = f"{shlex.quote(sys.executable)} -m claude_code_tools.amux"
    view_flags = (
        f" --dormant-hours {args.dormant_hours} --sort {args.sort}"
        + (" --dormant" if args.dormant else "")
    )
    # Keep user values out of fzf's action/placeholder parser as well as the shell.
    reload_env = dict(os.environ)
    if args.repo is not None:
        reload_env["AMUX_RELOAD_REPO"] = args.repo
        view_flags += ' --repo="$AMUX_RELOAD_REPO"'
    if args.directory is not None:
        reload_env["AMUX_RELOAD_DIR"] = args.directory
        view_flags += ' --dir="$AMUX_RELOAD_DIR"'
    binds = [
        f"ctrl-r:reload({self_cmd} rows --refresh{view_flags})",
        # Opening on cached rows is what makes this instant; refresh the
        # moment the list is up so stale states self-correct without a keypress.
        (
            f"load:reload-sync({self_cmd} rows --refresh{view_flags})"
            if from_cache else ""
        ),
    ]
    cmd = [
        "fzf",
        "--ansi",
        "--no-sort",
        # Field 1 is the pane target, hidden from view; see picker_lines().
        "--delimiter",
        "\t",
        "--with-nth",
        "2..",
        "--header",
        _FZF_HEADER,
        "--preview",
        "tmux capture-pane -ep -S -200 -t {1} | grep '[^[:space:]]' | tail -60",
        "--preview-window",
        "right:55%:wrap:follow",
        "--prompt",
        "agent> ",
    ]
    for bind in binds:
        if bind:
            cmd += ["--bind", bind]

    # Capture ONLY stdout. capture_output=True also pipes stderr, which is
    # where fzf draws its interface -- the UI never reaches the terminal and
    # the picker looks hung, waiting for keys against an invisible prompt.
    # (fzf reads keystrokes from /dev/tty, so feeding the list on stdin is
    # fine.)
    proc = subprocess.run(
        cmd,
        input=render.picker_lines(agents),
        stdout=subprocess.PIPE,
        text=True,
        env=reload_env,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return 0

    pane = render.pane_from_selection(proc.stdout)
    if not pane:
        return 0
    scan.switch_to(pane)
    return 0


def _finite_seconds(value: str) -> float:
    """Parse a --max-age value, rejecting nan/inf.

    argparse's ``type=float`` happily accepts "nan", and every comparison
    against NaN is False -- so ``--max-age nan`` disabled cache expiry
    entirely and served arbitrarily stale data.
    """
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError(
            f"must be a non-negative finite number of seconds, got {value!r}"
        )
    return seconds


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="amux",
        description="See and jump between coding agents running in tmux panes.",
    )
    parser.add_argument(
        "--max-age",
        type=_finite_seconds,
        default=30.0,
        help="seconds a cached scan stays usable (default: 30)",
    )
    sub = parser.add_subparsers(dest="command")

    pick = sub.add_parser("pick", help="interactive picker (default)")
    pick.set_defaults(func=cmd_pick)

    lst = sub.add_parser("list", help="print all live agents")
    lst.add_argument("--json", action="store_true", help="machine-readable output")
    lst.add_argument("--cached", action="store_true", help="use the cache if fresh")
    lst.add_argument("--no-color", action="store_true", help="disable colour")
    lst.set_defaults(func=cmd_list)

    scan_cmd = sub.add_parser("scan", help="refresh the cache")
    scan_cmd.set_defaults(func=cmd_scan)

    rows = sub.add_parser("rows", help=argparse.SUPPRESS)
    rows.add_argument("--refresh", action="store_true")
    rows.set_defaults(func=cmd_rows)

    for command in (pick, lst, rows, scan_cmd):
        command.add_argument(
            "--max-age", type=_finite_seconds, default=argparse.SUPPRESS,
            help="seconds a cached scan stays usable (default: 30)",
        )
    for command in (pick, lst, rows):
        command.add_argument(
            "--repo", "--project", dest="repo", metavar="NAME",
            help="exact repository name, including linked Git worktrees",
        )
        command.add_argument(
            "--dir", dest="directory", type=directory_argument, metavar="PATH",
            help="only agents in this directory or its subdirectories",
        )
        command.add_argument(
            "--dormant", action="store_true",
            help="show waiting agents with old known submitted input only",
        )
        command.add_argument(
            "--dormant-hours", type=_finite_seconds, default=72.0,
            help="input age in hours above which waiting agents are dormant (72)",
        )
        command.add_argument(
            "--sort", choices=("state", "oldest"), default="state",
            help="sort by current state or oldest known submitted input",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``amux`` console script."""
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    # Default to the picker, but insert the subcommand rather than reparsing
    # only ["pick"] -- reparsing dropped global options, so `amux --max-age 0`
    # silently used the 30s default.
    known = {"pick", "list", "scan", "rows"}
    value_options = {
        "--repo", "--project", "--dir", "--max-age", "--dormant-hours", "--sort",
    }
    tokens = iter(raw)
    explicit_command = False
    for token in tokens:
        if token in value_options:
            next(tokens, None)
        elif token in known | {"-h", "--help"}:
            explicit_command = True
            break
        elif not token.startswith("-"):
            break
    if not explicit_command:
        raw.insert(0, "pick")
    args = parser.parse_args(raw)
    if not scan.tmux_available():
        print("amux: no tmux server running", file=sys.stderr)
        return 1
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
