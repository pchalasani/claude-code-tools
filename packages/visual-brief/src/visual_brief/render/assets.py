"""Access to the committed front-end bundle shipped as package data.

The front end is built once with Vite and committed, so installing the tool
never needs Node. Reading the artifacts happens through ``importlib.resources``
so it works the same from a source checkout, a wheel and a zipped install.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from importlib import resources

STATIC_DIRECTORY = "static"
SCRIPT_NAME = "visual-brief.js"
STYLE_NAME = "visual-brief.css"
BOOTSTRAP_NAME = "visual-brief-theme-bootstrap.js"
DEFAULT_DESIGN = "catppuccin-mocha"
_DESIGN_ATTRIBUTES = {
    "north-window": ("north-window", "light", False),
    "blue-margin": ("blue-margin", "light", True),
    "dusk-margin": ("blue-margin", "dark", True),
    "solarized-paper": ("blue-margin", "light", True),
    "solarized-slate": ("blue-margin", "dark", True),
    "catppuccin-latte": ("blue-margin", "light", True),
    "catppuccin-mocha": ("blue-margin", "dark", True),
    "dusk-ledger": ("dusk-ledger", "dark", False),
    "night-ledger": ("night-ledger", "dark", False),
}
_REBUILD_HINT = "run `make visual-brief-frontend` and commit the result"
_ABSOLUTE_URL = re.compile(r"https?://")
_CLOSING_ELEMENT = re.compile(r"</(script|style)", re.IGNORECASE)
# `<!--` puts the HTML parser into the escaped script-data state, where the
# next `</script>` no longer closes the element.
_COMMENT_OPEN = "<!--"
# Control characters have no business in a bundle, and a NUL least of all: the
# HTML tokenizer rewrites it to U+FFFD without a word of complaint, so a page
# carrying one serves a script that is not the script that was built. The rest
# are parse errors. Tab, newline and carriage return are ordinary whitespace
# and stay welcome.
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class BundleError(RuntimeError):
    """Raised when the committed front-end bundle cannot be inlined."""


def _read(name: str) -> str:
    """Read one committed bundle artifact.

    Args:
        name: File name inside the package's static directory.

    Returns:
        The artifact's text.

    Raises:
        BundleError: If the artifact is missing or empty.
    """
    resource = resources.files("visual_brief").joinpath(STATIC_DIRECTORY, name)
    try:
        text = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as error:
        raise BundleError(
            f"the visual-brief front-end bundle {name} is missing; {_REBUILD_HINT}"
        ) from error
    if not text.strip():
        raise BundleError(
            f"the visual-brief front-end bundle {name} is empty; {_REBUILD_HINT}"
        )
    return text


def _require_inlinable(name: str, text: str) -> str:
    """Refuse a bundle that cannot be inlined into a single page.

    Args:
        name: Artifact name used in errors.
        text: Artifact text.

    Returns:
        The unchanged text.

    Raises:
        BundleError: If the text carries an absolute URL, a closing tag, a
            comment opener, or a control character — the first three escape
            the inline element's parser, and the last it refuses to serve
            back unchanged.
    """
    control = _CONTROL_CHARACTER.search(text)
    if control is not None:
        raise BundleError(
            f"{name} contains the control character "
            f"{ord(control.group(0)):#04x}, which the HTML parser reports as "
            f"an error and, for a NUL, silently rewrites; {_REBUILD_HINT}"
        )
    url = _ABSOLUTE_URL.search(text)
    if url is not None:
        raise BundleError(
            f"{name} contains the absolute URL {url.group(0)!r}; the page must "
            f"make zero external requests; {_REBUILD_HINT}"
        )
    closing = _CLOSING_ELEMENT.search(text)
    if closing is not None:
        raise BundleError(
            f"{name} contains {closing.group(0)!r}, which would end the inline "
            f"element early; {_REBUILD_HINT}"
        )
    if _COMMENT_OPEN in text:
        raise BundleError(
            f"{name} contains {_COMMENT_OPEN!r}, which would escape the "
            f"inline element's parser; {_REBUILD_HINT}"
        )
    return text


@lru_cache(maxsize=1)
def bundle_script() -> str:
    """Return the front-end JavaScript bundle.

    Returns:
        JavaScript safe to inline in a ``<script>`` element.
    """
    return _require_inlinable(SCRIPT_NAME, _read(SCRIPT_NAME))


@lru_cache(maxsize=1)
def bundle_style() -> str:
    """Return the front-end stylesheet.

    Returns:
        CSS safe to inline in a ``<style>`` element.
    """
    return _require_inlinable(STYLE_NAME, _read(STYLE_NAME))


@lru_cache(maxsize=1)
def bundle_bootstrap() -> str:
    """Return the tiny script that selects a theme before first paint.

    Returns:
        JavaScript safe to inline before the front-end stylesheet.
    """
    variants = json.dumps(_DESIGN_ATTRIBUTES, separators=(",", ":"))
    default = json.dumps(DEFAULT_DESIGN)
    script = (
        f"(()=>{{const v={variants},r=document.documentElement,"
        "q=new URLSearchParams(location.search).get('design'),"
        f"d=Object.hasOwn(v,q)?q:{default},m=v[d];"
        "r.dataset.design=d;r.dataset.designFamily=m[0];"
        "r.dataset.designMode=m[1];r.dataset.designPaired=String(m[2])}})();"
    )
    return _require_inlinable(BOOTSTRAP_NAME, script)


def stamp_bundle(
    script: str,
    style: str,
    bootstrap: str | None = None,
) -> str:
    """Return an identity for the inlined front-end artifacts.

    Args:
        script: The JavaScript bundle.
        style: The stylesheet.
        bootstrap: The first-paint theme script, or the packaged one by default.

    Returns:
        The SHA-256 of all three artifacts, as hex.
    """
    digest = hashlib.sha256()
    initial = bundle_bootstrap() if bootstrap is None else bootstrap
    artifacts = (
        (BOOTSTRAP_NAME, initial),
        (SCRIPT_NAME, script),
        (STYLE_NAME, style),
    )
    for name, text in artifacts:
        # Both the name and the length are part of the stamp, so no pair of
        # artifacts can be rearranged into the same byte stream as another.
        digest.update(f"{name}:{len(text)}:".encode("utf-8"))
        digest.update(text.encode("utf-8"))
    return digest.hexdigest()


@lru_cache(maxsize=1)
def bundle_stamp() -> str:
    """Return the identity of the front-end bundle a page inlines.

    It is derived from the bootstrap, script, and stylesheet and from nothing
    else, so it changes when the code changes and stays put when the document
    changes. That distinction is the whole point: an open page can tell a new
    document it may patch into itself from new code, which only a reload can
    load.

    Returns:
        The SHA-256 of the three inlined artifacts, as hex.
    """
    return stamp_bundle(bundle_script(), bundle_style())
