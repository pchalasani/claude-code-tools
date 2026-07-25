"""Render the local multi-run status dashboard."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Iterable

from visual_brief.server.registry import RunInfo


_REFRESH_MILLISECONDS = 5_000
_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f5f4ef; --card: #fffef9; --ink: #24231f; --muted: #68655d;
  --line: #d8d5cb; --accent: #245f78; --waiting: #8b4b0b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #171714; --card: #211f1b; --ink: #ede9df; --muted: #aaa69b;
    --line: #454239; --accent: #8bc3dc; --waiting: #f0ba75;
  }
}
* { box-sizing: border-box; }
body {
  background: var(--bg); color: var(--ink); margin: 0;
  font: 16px/1.5 ui-sans-serif, system-ui, -apple-system, sans-serif;
}
main { margin: 0 auto; max-width: 62rem; padding: 3rem 1rem 5rem; }
h1 { font-size: clamp(2rem, 5vw, 3.1rem); margin: 0; }
.deck { color: var(--muted); margin: .5rem 0 2rem; }
.runs { display: grid; gap: .8rem; list-style: none; margin: 0; padding: 0; }
.run {
  background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 1.15rem 1.25rem;
}
.run-head { align-items: start; display: flex; gap: 1rem;
  justify-content: space-between; }
h2 { font-size: 1.08rem; margin: 0; overflow-wrap: anywhere; }
.when, .context, .empty { color: var(--muted); }
.when { font-size: .8rem; white-space: nowrap; }
.context { font-size: .88rem; margin: .3rem 0 .7rem; }
.badge {
  border: 1px solid currentColor; border-radius: 999px; color: var(--waiting);
  display: inline-block; font-size: .72rem; font-weight: 750;
  margin-top: .35rem; padding: .2rem .45rem;
}
.degraded { color: var(--muted); }
.links { display: flex; flex-wrap: wrap; gap: .5rem 1rem; }
a { color: var(--accent); }
.empty {
  background: var(--card); border: 1px dashed var(--line);
  border-radius: 8px; padding: 2rem; text-align: center;
}
@media (max-width: 36rem) {
  main { padding-top: 2rem; }
  .run-head { display: block; }
  .when { display: block; margin-top: .3rem; }
}
"""


def render_dashboard(
    runs: Iterable[RunInfo],
    port: int,
    *,
    now: datetime | None = None,
) -> str:
    """Render a self-contained dashboard page.

    Args:
        runs: Run summaries, normally ordered by the registry.
        port: Daemon port used to build both run URL forms.
        now: Optional clock value for deterministic relative times.

    Returns:
        A complete HTML document.

    Raises:
        ValueError: If the port is outside the valid TCP port range.
    """
    if not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    rows = "".join(_render_run(run, port, current) for run in runs)
    if not rows:
        rows = (
            '<div class="empty">No visual brief runs yet. '
            "Create one with <code>visual-brief new</code>.</div>"
        )
    script = (
        "window.setInterval(() => window.location.reload(), "
        f"{_REFRESH_MILLISECONDS});"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="icon" href="data:,">'
        f"<title>Visual brief runs</title><style>{_CSS}</style></head><body>"
        '<main><div class="eyebrow">LOCAL STATUS BOARD</div>'
        "<h1>Visual brief runs</h1>"
        '<p class="deck">Active briefings, newest activity first.</p>'
        f'<section aria-label="Runs" class="runs">{rows}</section>'
        f"</main><script>{script}</script></body></html>"
    )


def humanize_activity(activity: datetime, now: datetime) -> str:
    """Describe an activity timestamp relative to a clock value.

    Args:
        activity: Timestamp of the run's newest activity.
        now: Current time.

    Returns:
        A compact human-readable age.
    """
    if activity.tzinfo is None:
        activity = activity.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    seconds = max(0, int((now - activity).total_seconds()))
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{seconds} seconds ago"
    minutes = seconds // 60
    if minutes < 60:
        return _units_ago(minutes, "minute")
    hours = minutes // 60
    if hours < 24:
        return _units_ago(hours, "hour")
    days = hours // 24
    if days < 30:
        return _units_ago(days, "day")
    months = days // 30
    if months < 12:
        return _units_ago(months, "month")
    return _units_ago(days // 365, "year")


def _render_run(run: RunInfo, port: int, now: datetime) -> str:
    """Render one dashboard run card."""
    run_id = _escape(run.run_id)
    subdomain_url = f"http://{run_id}.localhost:{port}/"
    path_url = f"http://localhost:{port}/r/{run_id}/"
    context_parts = [
        _escape(value) for value in (run.repo, run.branch) if value is not None
    ]
    context = " · ".join(context_parts)
    context_html = (
        f'<p class="context">{context}</p>' if context else ""
    )
    waiting = ""
    if run.unanswered_count:
        count = run.unanswered_count
        noun = "question" if count == 1 else "questions"
        waiting = (
            f'<span class="badge">waiting on you · {count} {noun}</span>'
        )
    degraded = (
        '<span class="badge degraded">metadata unavailable</span>'
        if run.degraded
        else ""
    )
    changed = _escape(humanize_activity(run.activity_at, now))
    return (
        '<article class="run"><div class="run-head"><div>'
        f"<h2>{_escape(run.label)}</h2>{context_html}{waiting}{degraded}"
        f'</div><time class="when">{changed}</time></div>'
        '<div class="links">'
        f'<a href="{subdomain_url}">subdomain</a>'
        f'<a href="{path_url}">path fallback</a>'
        "</div></article>"
    )


def _units_ago(count: int, unit: str) -> str:
    """Format a relative-time quantity."""
    suffix = "" if count == 1 else "s"
    return f"{count} {unit}{suffix} ago"


def _escape(value: object) -> str:
    """Escape untrusted dashboard text."""
    return html.escape(str(value), quote=True)
