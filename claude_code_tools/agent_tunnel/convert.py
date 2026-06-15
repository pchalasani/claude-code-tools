"""Best-effort conversion of attached Office files into something the fork's
``Read`` tool can open.

The ``Read`` tool natively handles PDF, images, and text — but **not** binary
Office formats (``.docx``/``.pptx``/``.xlsx`` are zipped XML). When a colleague
attaches one, we opportunistically convert it using whatever converter already
exists on the host's ``PATH``; if none is found this is a clean no-op (the
colleague is told to send a PDF instead). **No converter is ever a hard
dependency.**

Fidelity order (best first):

1. **LibreOffice → PDF** (``soffice``/``libreoffice``): handles every Office
   type, and PDF is what ``Read`` renders best (tables/diagrams/equations are
   seen as laid out). Each run uses an isolated profile dir so concurrent
   conversions don't fight over LibreOffice's lock.
2. **pandoc → Markdown** (with media extracted): great for word-processor docs
   (text, tables, footnotes, images pulled out as readable files); pandoc can't
   read slides/sheets, so it's used only for doc formats.
3. **textutil → text** (macOS only): always present on a Mac but lossy
   (tables flatten, footnote bodies and images drop).

An owner can override the chain with a custom command template
(``{input}``/``{outdir}`` placeholders); its output is discovered by diffing
``{outdir}`` before/after.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .paths import changed_files, safe_key, snapshot_dir

# Binary Office formats the Read tool cannot open and that we try to convert.
CONVERTIBLE_EXTS = {
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".xlsx",
    ".xls",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
}
# Formats pandoc / textutil can actually read (not slides or spreadsheets).
_PANDOC_EXTS = {".docx", ".odt", ".rtf", ".html", ".htm", ".epub"}
_TEXTUTIL_EXTS = {".docx", ".doc", ".odt", ".rtf", ".html", ".htm"}

_TIMEOUT_S = 120


@dataclass
class Conversion:
    """Outcome of converting one attachment."""

    # The readable file to hand the fork (PDF/Markdown/text), or None if
    # nothing on the host could convert it.
    path: Optional[Path]
    # Label of the converter used (e.g. "libreoffice→pdf"); "" if none ran.
    converter: str


def _which(*names: str) -> Optional[str]:
    """First of `names` found on PATH, or None."""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def converters_available() -> bool:
    """True if any built-in converter is present on the host."""
    return _which("soffice", "libreoffice", "pandoc", "textutil") is not None


def detect_converter() -> str:
    """Human label of the converter that auto mode would pick, '' if none."""
    if _which("soffice", "libreoffice"):
        return "LibreOffice → PDF"
    if _which("pandoc"):
        return "pandoc → Markdown"
    if _which("textutil"):
        return "textutil → text (macOS)"
    return ""


def _run(argv: list[str]) -> None:
    """Run a converter, raising on failure or timeout."""
    subprocess.run(
        argv, capture_output=True, timeout=_TIMEOUT_S, check=True
    )


def _libreoffice(src: Path, outdir: Path, binary: str) -> Optional[Path]:
    """Convert to PDF via headless LibreOffice (isolated profile)."""
    profile = (outdir / "lo-profile").resolve().as_uri()
    _run(
        [
            binary,
            "--headless",
            f"-env:UserInstallation={profile}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(src),
        ]
    )
    out = outdir / f"{src.stem}.pdf"
    return out if out.exists() else None


def _pandoc(src: Path, outdir: Path, binary: str) -> Optional[Path]:
    """Convert to GitHub-flavored Markdown, extracting embedded media."""
    out = outdir / f"{src.stem}.md"
    _run(
        [
            binary,
            str(src),
            "-t",
            "gfm",
            "--extract-media",
            str(outdir / "media"),
            "-o",
            str(out),
        ]
    )
    return out if out.exists() else None


def _textutil(src: Path, outdir: Path, binary: str) -> Optional[Path]:
    """Convert to plain text via macOS textutil (lossy fallback)."""
    out = outdir / f"{src.stem}.txt"
    _run([binary, "-convert", "txt", "-output", str(out), str(src)])
    return out if out.exists() else None


def _run_custom(template: str, src: Path, outdir: Path) -> Optional[Path]:
    """Run an owner-configured command template; discover its output by diff.

    The template (trusted owner config, not colleague input) is shell-expanded
    with ``{input}``/``{outdir}`` substituted; whatever new file appears in
    ``outdir`` is taken as the result.
    """
    before = snapshot_dir(outdir)
    cmd = template.format(
        input=shlex.quote(str(src)), outdir=shlex.quote(str(outdir))
    )
    subprocess.run(
        cmd, shell=True, capture_output=True, timeout=_TIMEOUT_S, check=True
    )
    produced = changed_files(outdir, before)
    return produced[0] if produced else None


def convert_attachment(
    src: Path,
    work_dir: Path,
    mode: str = "auto",
    custom_command: str = "",
) -> Conversion:
    """Best-effort convert `src` into a Read-openable file.

    Args:
        src: The downloaded attachment.
        work_dir: Dir to write converted output under (the thread's upload dir,
            which is already exposed to the fork via ``--add-dir``).
        mode: "auto" to use the best available converter, "off" to skip.
        custom_command: Optional command template overriding auto-detection.

    Returns:
        A Conversion; ``path`` is None when conversion is off, the type is not
        convertible, or no converter on the host could handle it.
    """
    ext = src.suffix.lower()
    if mode == "off" or ext not in CONVERTIBLE_EXTS:
        return Conversion(path=None, converter="")

    outdir = work_dir / "converted" / safe_key(src.stem)
    outdir.mkdir(parents=True, exist_ok=True)

    if custom_command:
        try:
            out = _run_custom(custom_command, src, outdir)
            if out:
                return Conversion(path=out, converter="custom")
        except Exception:
            pass
        return Conversion(path=None, converter="")

    # 1) LibreOffice → PDF (every Office type; highest fidelity for Read).
    soffice = _which("soffice", "libreoffice")
    if soffice:
        try:
            out = _libreoffice(src, outdir, soffice)
            if out:
                return Conversion(path=out, converter="libreoffice→pdf")
        except Exception:
            pass
    # 2) pandoc → Markdown (word-processor formats only).
    if ext in _PANDOC_EXTS:
        pandoc = _which("pandoc")
        if pandoc:
            try:
                out = _pandoc(src, outdir, pandoc)
                if out:
                    return Conversion(path=out, converter="pandoc→md")
            except Exception:
                pass
    # 3) textutil → text (macOS; word-processor formats only).
    if ext in _TEXTUTIL_EXTS:
        textutil = _which("textutil")
        if textutil:
            try:
                out = _textutil(src, outdir, textutil)
                if out:
                    return Conversion(path=out, converter="textutil→txt")
            except Exception:
                pass
    return Conversion(path=None, converter="")
