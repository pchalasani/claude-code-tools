"""Run every fixture in one split against Jev and retain complete evidence."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from jev_prose.backend import make_backend
from jev_prose.detector import check, load_bank


def metrics(rows: list[dict[str, Any]], threshold: float) -> dict[str, int | float]:
    """Count passage-level positives; labels do not identify individual defects."""
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for row in rows:
        positive = any(j["probability"] >= threshold
                       for j in row["report"]["judgments"])
        label = row["expect_findings"]
        counts[("t" if positive == label else "f") + ("p" if positive else "n")] += 1
    return {"threshold": threshold, **counts}


def main() -> int:
    """Run a named split without silently accepting an incomplete evaluation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("calibration", "holdout"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path,
                        default=Path(__file__).with_name("cases.jsonl"))
    parser.add_argument("--threshold", type=float, default=0.9)
    args = parser.parse_args()
    fixture = args.cases
    cases = [json.loads(line) for line in fixture.read_text().splitlines()]
    selected = [row for row in cases if row["split"] == args.split]
    if not selected:
        raise ValueError("No cases selected")
    bank, digest = load_bank()
    backend = make_backend("cloudflare", None, None, 90)
    rows: list[dict[str, Any]] = []
    failure: str | None = None
    for case in selected:
        started = time.monotonic()
        try:
            report = check(case["text"], backend, bank, digest,
                           profile=case["profile"], threshold=args.threshold)
            if not report["ok"] or report["answered"] != report["expected"]:
                raise ValueError("Incomplete check")
        except Exception as exc:
            failure = f"{case['id']}: {type(exc).__name__}: {exc}"
            break
        rows.append({**case, "seconds": round(time.monotonic() - started, 3),
                     "report": report})
        print(json.dumps({"ran": True, "ok": True, "id": case["id"],
                          "findings": [f["id"] for f in report["findings"]]}),
              flush=True)
    complete = len(rows) == len(selected) and failure is None
    result = {"ran": backend.attempted, "ok": complete,
              "split": args.split, "expected": len(selected), "answered": len(rows),
              "bank_sha256": digest, "error": failure, "rows": rows}
    if complete:
        grid = ([0.65, 0.75, 0.85, 0.9, 0.95, 0.98]
                if args.split == "calibration" else [args.threshold])
        result["metrics"] = [metrics(rows, threshold) for threshold in grid]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}))
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
