"""Assembly of self-contained visual brief pages."""

from __future__ import annotations

import hashlib
from typing import Any

from .css import CSS
from .html import escape, render_timeline
from .script import JS
from .validate import TRUST_LABELS

_GENERATION_PLACEHOLDER = "0" * 64

CONTROLS = """
<nav class="key-controls" aria-label="Keyboard and page controls">
  <button class="key-control" data-action="previous-item">k · Previous item</button>
  <button class="key-control" data-action="next-item">j · Next item</button>
  <button class="key-control" data-action="previous-lane">K · Previous lane</button>
  <button class="key-control" data-action="next-lane">J · Next lane</button>
  <button class="key-control" data-action="next-awaiting">n · Awaiting</button>
  <button class="key-control" data-action="search">/ · Search</button>
  <button class="key-control" data-action="top">g · Top</button>
  <button class="key-control" data-action="bottom">G · Bottom</button>
  <button class="key-control help-control" data-action="help"
    aria-haspopup="dialog">? · Keys</button>
</nav>
<div class="search-panel" id="search-panel" hidden>
  <label for="page-search">Search items</label>
  <input id="page-search" type="search" autocomplete="off">
  <span id="match-count" role="status"></span>
  <button id="close-search" type="button">Escape · Close</button>
</div>
<dialog id="key-help" aria-labelledby="key-help-title">
  <h2 id="key-help-title">Keyboard controls</h2>
  <dl>
    <dt>j / k</dt><dd>Next / previous item</dd>
    <dt>J / K</dt><dd>Next / previous lane</dd>
    <dt>Space</dt><dd>Expand or collapse the focused disclosure</dd>
    <dt>a</dt><dd>Ask about the focused item or lane</dd>
    <dt>n</dt><dd>Next thread awaiting an answer</dd>
    <dt>/</dt><dd>Search items</dd>
    <dt>g / G</dt><dd>Top / bottom</dd>
    <dt>?</dt><dd>Show this key list</dd>
    <dt>Escape</dt><dd>Close or leave a text box</dd>
  </dl>
  <button id="close-help" type="button">Escape · Close</button>
</dialog>
"""


def render_page(data: dict[str, Any]) -> str:
    """Assemble a validated visual brief into one HTML document.

    Args:
        data: A validated visual brief document.

    Returns:
        A self-contained HTML document.
    """
    legend = "".join(
        f'<span class="chip {key}">{label}</span>'
        for key, label in TRUST_LABELS.items()
    )
    timeline = render_timeline(data["updates"])
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="visual-brief-render-version" '
        f'content="{_GENERATION_PLACEHOLDER}">'
        '<link rel="icon" href="data:,">'
        f"<title>{escape(data['title'])}</title><style>{CSS}</style></head>"
        '<body><main class="page"><div class="eyebrow">Session briefing</div>'
        f'<h1>{escape(data["title"])}</h1>'
        f'<p class="deck">{escape(data["summary"])}</p>'
        f"{CONTROLS}"
        f'<aside class="legend"><span class="legend-label">TRUST</span>{legend}'
        f'</aside><section aria-label="Session timeline">{timeline}</section>'
        f"</main><script>{JS}</script></body></html>"
    )
    generation = hashlib.sha256(page.encode("utf-8")).hexdigest()
    return page.replace(_GENERATION_PLACEHOLDER, generation, 1)
