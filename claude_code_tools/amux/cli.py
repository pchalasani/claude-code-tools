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
from .model import Agent

_FZF_HEADER = (
    "enter=jump  ctrl-r=refresh  esc=quit    "
    "input=asking you  busy=working  bg=monitors  idle"
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


def cmd_list(args: argparse.Namespace) -> int:
    """Print every live agent as a table or JSON."""
    agents = cache.read(max_age=args.max_age)[0] if args.cached else _fresh()
    if not agents and args.cached:
        agents = _fresh()
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
    print(render.picker_lines(agents, colour=True))
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
    if not agents:
        print("no agents running")
        return 0

    # fzf runs bind commands through a shell, so the interpreter path must be
    # quoted: a venv at "/tmp/my venv/bin/python" would otherwise run "/tmp/my".
    self_cmd = f"{shlex.quote(sys.executable)} -m claude_code_tools.amux"
    binds = [
        f"ctrl-r:reload({self_cmd} rows --refresh)",
        # Opening on cached rows is what makes this instant; refresh the
        # moment the list is up so stale states self-correct without a keypress.
        f"load:reload-sync({self_cmd} rows --refresh)" if from_cache else "",
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

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``amux`` console script."""
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    # Default to the picker, but insert the subcommand rather than reparsing
    # only ["pick"] -- reparsing dropped global options, so `amux --max-age 0`
    # silently used the 30s default.
    known = {"pick", "list", "scan", "rows"}
    if not any(tok in known for tok in raw):
        raw.append("pick")
    args = parser.parse_args(raw)
    if not scan.tmux_available():
        print("amux: no tmux server running", file=sys.stderr)
        return 1
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
