"""CLI for complete, machine-readable Jev prose checks."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from .backend import Backend, DetectorError, make_backend
from .detector import PROFILES, check, load_bank
from .install import install_skill


def parser() -> argparse.ArgumentParser:
    """Define a small interface suitable for both human and agent callers."""
    result = argparse.ArgumentParser(prog="jev-prose")
    commands = result.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install-skill", help="Install the global skill")
    install.add_argument("--target", choices=("claude", "codex", "both"),
                         default="both")
    install.add_argument("--force", action="store_true")
    bank = commands.add_parser("questions", help="Print bank without model calls")
    bank.add_argument("--bank", type=Path)
    scan = commands.add_parser("check", help="Check a short prose excerpt")
    scan.add_argument("file", nargs="?", default="-", help="UTF-8 file or stdin (-)")
    scan.add_argument("--profile", choices=PROFILES, default="general")
    scan.add_argument("--context", type=Path, help="Background only, not target prose")
    scan.add_argument("--audience", default="")
    scan.add_argument("--voice", default="")
    scan.add_argument("--threshold", type=float, default=0.9)
    scan.add_argument("--bank", type=Path, help="Alternative JSON question bank")
    scan.add_argument("--backend", choices=("cloudflare", "typesafe"),
                      default="cloudflare")
    scan.add_argument("--url", help="Explicit compatible /v1/systemone endpoint")
    scan.add_argument("--model", help="Model name/version override")
    scan.add_argument("--timeout", type=float, default=90)
    scan.add_argument("--batch-size", type=int, default=24)
    scan.add_argument("--workers", type=int, default=4)
    scan.add_argument("--format", choices=("json", "text"), default="json")
    return result


def emit(report: dict[str, Any], output_format: str = "json") -> None:
    """Render results without echoing private input prose or credentials."""
    if output_format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    elif not report["ok"]:
        print(f"ERROR (not a passing check): {report['error']}")
    else:
        print(f"{report['status']}: {report['answered']}/{report['expected']} "
              f"questions; model {report['model']}; threshold {report['threshold']}")
        for finding in report["findings"]:
            print(f"- {finding['id']} ({finding['probability']:.3f}): "
                  f"{finding['title']}\n  {finding['guidance']}")


def main(argv: list[str] | None = None) -> int:
    """Return 0 clean, 1 findings, or 2 failure; never edit the input."""
    args = parser().parse_args(argv)
    backend: Backend | None = None
    try:
        if args.command == "install-skill":
            paths = install_skill(args.target, args.force)
            emit({"ran": True, "ok": True, "installed": paths})
            return 0
        bank, digest = load_bank(args.bank)
        if args.command == "questions":
            emit({"ran": True, "ok": True, "bank_sha256": digest, **bank})
            return 0
        text = sys.stdin.read(24_001) if args.file == "-" else Path(
            args.file
        ).read_text(encoding="utf-8")
        context = args.context.read_text(encoding="utf-8") if args.context else ""
        if not math.isfinite(args.timeout) or not 0 < args.timeout <= 300:
            raise DetectorError("Timeout must be finite and in (0, 300].")
        backend = make_backend(args.backend, args.url, args.model, args.timeout)
        report = check(
            text, backend, bank, digest, profile=args.profile,
            threshold=args.threshold, context=context, audience=args.audience,
            voice=args.voice, batch_size=args.batch_size, workers=args.workers,
        )
        emit(report, args.format)
        return 1 if report["findings"] else 0
    except (DetectorError, OSError, ValueError, TypeError) as exc:
        emit({"ran": backend.attempted if backend else False,
              "ok": False, "status": "error", "error": str(exc)},
             getattr(args, "format", "json"))
        return 2


if __name__ == "__main__":
    sys.exit(main())
