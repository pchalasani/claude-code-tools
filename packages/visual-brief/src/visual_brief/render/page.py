"""Assembly of self-contained visual brief pages.

The page is a shell: a mount point, the validated document as an embedded JSON
blob, and the front-end bundle inlined as one stylesheet and one script. The
document's content and its identity still come from Python; the front end
renders it.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .assets import bundle_script, bundle_stamp, bundle_style
from .embed import DOCUMENT_ELEMENT_ID, ROOT_ELEMENT_ID, embed_document, escape

_GENERATION_PLACEHOLDER = "0" * 64
# How often the delivered page checks whether it is still current. It is
# written into the page rather than compiled into the bundle so that one
# served page can be asked to check faster than another.
POLL_INTERVAL_MS = 5000
_NOSCRIPT = (
    "This briefing is an interactive page and needs JavaScript, which is "
    "already running locally on your own machine."
)


def render_page(data: dict[str, Any]) -> str:
    """Assemble a validated visual brief into one HTML document.

    Args:
        data: A validated visual brief document.

    Returns:
        A self-contained HTML document that makes no external requests.
    """
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="visual-brief-render-version" '
        f'content="{_GENERATION_PLACEHOLDER}">'
        # What a generation change MEANS depends on this stamp. New content is
        # patched into the open page; new code can only arrive by reloading it,
        # and a page that patched a document into last week's bundle would run
        # last week's bundle for the rest of its life.
        f'<meta name="visual-brief-assets-version" content="{bundle_stamp()}">'
        f'<meta name="visual-brief-poll-ms" content="{POLL_INTERVAL_MS}">'
        '<link rel="icon" href="data:,">'
        f"<title>{escape(data['title'])}</title>"
        f"<style>{bundle_style()}</style></head>"
        f'<body><div id="{ROOT_ELEMENT_ID}"></div>'
        f"<noscript>{escape(_NOSCRIPT)}</noscript>"
        f'<script type="application/json" id="{DOCUMENT_ELEMENT_ID}">'
        f"{embed_document(data)}</script>"
        f"<script>{bundle_script()}</script></body></html>"
    )
    generation = hashlib.sha256(page.encode("utf-8")).hexdigest()
    return page.replace(_GENERATION_PLACEHOLDER, generation, 1)
