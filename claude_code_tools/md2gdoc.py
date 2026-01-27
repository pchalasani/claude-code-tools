#!/usr/bin/env python3
"""
md2gdoc: Upload Markdown files to Google Drive as native Google Docs.

This tool uses the Google Drive API to upload markdown files with native
conversion to Google Docs format - the same conversion that happens when
you manually upload a .md file and click "Open in Google Docs".

Images referenced in markdown are uploaded to Drive at full resolution,
then inserted into the Google Doc via the Docs API with proper sizing
to fit within the page width.

Prerequisites:
- First run: Will open browser for OAuth authentication (one-time setup)
- Credentials stored in ~/.config/md2gdoc/
"""

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

console = Console()

# Google API scopes
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

# Max display width for images in points.
# Letter paper (8.5") with 1" margins = 6.5" content = 468pt.
MAX_IMAGE_WIDTH_PT = 468

# Config directory for storing credentials (global fallback)
CONFIG_DIR = Path.home() / ".config" / "md2gdoc"

# Local (project-specific) credential files - checked first
LOCAL_TOKEN_FILE = Path(".gdoc-token.json")
LOCAL_CREDENTIALS_FILE = Path(".gdoc-credentials.json")

# Global credential files - fallback
GLOBAL_TOKEN_FILE = CONFIG_DIR / "token.json"
GLOBAL_CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"


def get_token_file() -> Path:
    """Get token file path - local first, then global."""
    if LOCAL_TOKEN_FILE.exists():
        return LOCAL_TOKEN_FILE
    return GLOBAL_TOKEN_FILE


def get_credentials_file() -> Path:
    """Get credentials file path - local first, then global."""
    if LOCAL_CREDENTIALS_FILE.exists():
        return LOCAL_CREDENTIALS_FILE
    if GLOBAL_CREDENTIALS_FILE.exists():
        return GLOBAL_CREDENTIALS_FILE
    return LOCAL_CREDENTIALS_FILE


# Default OAuth client credentials (for CLI tools)
DEFAULT_CLIENT_CONFIG = {
    "installed": {
        "client_id": (
            "YOUR_CLIENT_ID.apps.googleusercontent.com"
        ),
        "project_id": "md2gdoc",
        "auth_uri": (
            "https://accounts.google.com/o/oauth2/auth"
        ),
        "token_uri": (
            "https://oauth2.googleapis.com/token"
        ),
        "auth_provider_x509_cert_url": (
            "https://www.googleapis.com/oauth2/v1/certs"
        ),
        "client_secret": "YOUR_CLIENT_SECRET",
        "redirect_uris": ["http://localhost"],
    }
}


def check_dependencies() -> bool:
    """Check if required Google API packages are installed."""
    try:
        from google.oauth2.credentials import Credentials  # noqa
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa
        from googleapiclient.discovery import build  # noqa

        return True
    except ImportError:
        console.print(
            Panel(
                "[red]Missing dependencies[/red]\n\n"
                "Install with:\n"
                "[cyan]pip install google-api-python-client"
                " google-auth-oauthlib Pillow[/cyan]",
                title="Dependencies Required",
                border_style="red",
            )
        )
        return False


_cached_creds = None


def get_credentials():
    """Get OAuth credentials with caching.

    Tries in order:
    1. Cached credentials from this session
    2. Saved token (local .gdoc-token.json, then global)
    3. Application Default Credentials (gcloud)
    4. Manual OAuth flow using credentials.json
    """
    global _cached_creds
    if _cached_creds and _cached_creds.valid:
        return _cached_creds

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    token_file = get_token_file()
    credentials_file = get_credentials_file()

    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(token_file), SCOPES
            )
            if creds and creds.valid:
                console.print(
                    f"[dim]Using token: {token_file}[/dim]"
                )
                _cached_creds = creds
                return creds
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_file, "w") as f:
                    f.write(creds.to_json())
                console.print(
                    f"[dim]Using token: {token_file}[/dim]"
                )
                _cached_creds = creds
                return creds
        except Exception:
            pass

    try:
        import google.auth

        creds, _ = google.auth.default(scopes=SCOPES)
        if creds and creds.valid:
            console.print(
                "[dim]Using Application Default "
                "Credentials[/dim]"
            )
            _cached_creds = creds
            return creds
    except Exception:
        pass

    from google_auth_oauthlib.flow import InstalledAppFlow

    if not credentials_file.exists():
        console.print(
            Panel(
                "[red]OAuth credentials not found![/red]\n\n"
                "[bold]Option A - Project-specific:[/bold]\n"
                "Save OAuth client JSON as: "
                "[cyan].gdoc-credentials.json[/cyan]\n\n"
                "[bold]Option B - Global:[/bold]\n"
                f"Save as: [cyan]{GLOBAL_CREDENTIALS_FILE}"
                "[/cyan]\n\n"
                "[bold]To get the OAuth client JSON:[/bold]\n"
                "1. Go to console.cloud.google.com\n"
                "2. APIs & Services → Credentials\n"
                "3. Create OAuth client ID → Desktop app\n"
                "4. Download JSON",
                title="Setup Required",
                border_style="yellow",
            )
        )
        return None

    console.print(
        f"[dim]Using credentials: {credentials_file}[/dim]"
    )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_file), SCOPES
    )
    console.print(
        "[cyan]Opening browser for Google "
        "authentication...[/cyan]"
    )
    creds = flow.run_local_server(port=0)

    with open(LOCAL_TOKEN_FILE, "w") as token:
        token.write(creds.to_json())
    console.print(
        f"[green]Token saved to {LOCAL_TOKEN_FILE}[/green]"
    )

    _cached_creds = creds
    return creds


def get_drive_service():
    """Get authenticated Google Drive service."""
    from googleapiclient.discovery import build

    creds = get_credentials()
    if not creds:
        return None
    return build("drive", "v3", credentials=creds)


def get_docs_service():
    """Get authenticated Google Docs service."""
    from googleapiclient.discovery import build

    creds = get_credentials()
    if not creds:
        return None
    return build("docs", "v1", credentials=creds)


def find_folder_id(
    service,
    folder_path: str,
    create_if_missing: bool = True,
) -> Optional[str]:
    """Find folder by path, optionally create if missing."""
    if not folder_path:
        return None

    parts = folder_path.strip("/").split("/")
    parent_id = "root"

    for part in parts:
        query = (
            f"name = '{part}' and "
            f"'{parent_id}' in parents and "
            f"(mimeType = 'application/vnd.google-apps.folder'"
            f" or mimeType = "
            f"'application/vnd.google-apps.shortcut') and "
            f"trashed = false"
        )
        console.print(
            f"[dim]Looking for '{part}' in "
            f"parent={parent_id}[/dim]"
        )
        results = (
            service.files()
            .list(
                q=query,
                fields=(
                    "files(id, name, mimeType,"
                    " shortcutDetails)"
                ),
                pageSize=10,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files = results.get("files", [])
        console.print(
            f"[dim]Found {len(files)} matches: "
            f"{[(f['name'], f['mimeType']) for f in files]}"
            f"[/dim]"
        )

        if files:
            file = files[0]
            mime = file.get("mimeType")
            if mime == "application/vnd.google-apps.shortcut":
                details = file.get("shortcutDetails", {})
                target_id = details.get("targetId")
                if target_id:
                    console.print(
                        f"[dim]Following shortcut: "
                        f"{part}[/dim]"
                    )
                    parent_id = target_id
                else:
                    console.print(
                        f"[yellow]Warning: Shortcut "
                        f"{part} has no target[/yellow]"
                    )
                    return None
            else:
                parent_id = file["id"]
        else:
            if not create_if_missing:
                return None
            file_metadata = {
                "name": part,
                "mimeType": (
                    "application/vnd.google-apps.folder"
                ),
                "parents": [parent_id],
            }
            folder = (
                service.files()
                .create(body=file_metadata, fields="id")
                .execute()
            )
            parent_id = folder["id"]
            console.print(
                f"[dim]Created folder: {part}[/dim]"
            )

    return parent_id


def check_file_exists(
    service, folder_id: Optional[str], filename: str
) -> bool:
    """Check if a file with this name exists."""
    parent = folder_id if folder_id else "root"
    query = (
        f"name = '{filename}' and "
        f"'{parent}' in parents and "
        f"mimeType = 'application/vnd.google-apps.document'"
        f" and trashed = false"
    )
    console.print(
        f"[dim]Checking for existing file: "
        f"{filename}[/dim]"
    )
    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name)",
            pageSize=10,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = results.get("files", [])
    if files:
        console.print(
            f"[dim]Found {len(files)} existing file(s): "
            f"{[f['name'] for f in files]}[/dim]"
        )
    return len(files) > 0


def list_existing_versions(
    service, folder_id: Optional[str], base_name: str
) -> list[str]:
    """List files matching the base name pattern."""
    parent = folder_id if folder_id else "root"
    query = (
        f"name contains '{base_name}' and "
        f"'{parent}' in parents and "
        f"mimeType = 'application/vnd.google-apps.document'"
        f" and trashed = false"
    )
    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name)",
            pageSize=100,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return [f["name"] for f in results.get("files", [])]


def get_next_version_name(
    service, folder_id: Optional[str], base_name: str
) -> str:
    """Get next available version name."""
    existing = list_existing_versions(
        service, folder_id, base_name
    )
    if not existing:
        return f"{base_name}-1"

    max_version = 0
    for f in existing:
        match = re.match(
            rf"^{re.escape(base_name)}-(\d+)$", f
        )
        if match:
            max_version = max(
                max_version, int(match.group(1))
            )
    return f"{base_name}-{max_version + 1}"


def delete_file(
    service, folder_id: Optional[str], filename: str
) -> bool:
    """Delete a file by name (for overwrite)."""
    parent = folder_id if folder_id else "root"
    query = (
        f"name = '{filename}' and "
        f"'{parent}' in parents and "
        f"mimeType = 'application/vnd.google-apps.document'"
        f" and trashed = false"
    )
    results = (
        service.files()
        .list(
            q=query,
            fields="files(id)",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = results.get("files", [])
    if files:
        service.files().delete(
            fileId=files[0]["id"],
            supportsAllDrives=True,
        ).execute()
        return True
    return False


def prompt_for_conflict(
    existing_name: str, versioned_name: str
) -> Optional[str]:
    """Prompt user when file already exists."""
    console.print()
    console.print(
        Panel(
            f"[yellow]File already exists:[/yellow] "
            f"[bold]{existing_name}[/bold]",
            title="Conflict Detected",
            border_style="yellow",
        )
    )
    console.print()

    options_text = Text()
    options_text.append("  [Enter] ", style="cyan bold")
    options_text.append("Add version suffix → ", style="dim")
    options_text.append(f"{versioned_name}\n", style="green")
    options_text.append("  [YES]   ", style="red bold")
    options_text.append("Overwrite existing file", style="dim")
    console.print(options_text)
    console.print()

    choice = Prompt.ask(
        "Your choice", default="", show_default=False
    )
    if choice == "":
        return "version"
    elif choice.upper() == "YES":
        return "overwrite"
    else:
        console.print("[dim]Invalid choice. Cancelling.[/dim]")
        return None


# --- Image handling ---


@dataclass
class ImageInfo:
    """Metadata for an image to be inserted."""

    placeholder: str
    drive_url: str
    file_id: str
    width_px: int
    height_px: int


def strip_local_image_refs(content: str) -> str:
    """Replace local image refs with bold alt text.

    Google's markdown converter crashes (HTTP 500) on
    unresolvable image paths. This strips them.
    """
    pattern = r"!\[([^\]]*)\]\(([^)\s]+)\)"

    def _replace(match: re.Match) -> str:
        ref_path = match.group(2)
        if ref_path.startswith(
            ("http://", "https://", "data:")
        ):
            return match.group(0)
        alt = match.group(1)
        console.print(
            f"  [dim]Stripped: {ref_path}[/dim]"
        )
        return f"**[Image: {alt}]**"

    return re.sub(pattern, _replace, content)


def find_local_images(
    content: str,
    md_dir: Path,
) -> tuple[
    list[tuple[str, str, Path]],
    list[tuple[str, str, str]],
]:
    """Find local image references in markdown.

    Returns (found, missing) where each is a list of
    (full_match, alt_text, path_or_ref).
    """
    pattern = r"!\[([^\]]*)\]\(([^)\s]+)\)"
    found: list[tuple[str, str, Path]] = []
    missing: list[tuple[str, str, str]] = []
    for match in re.finditer(pattern, content):
        alt_text = match.group(1)
        ref_path = match.group(2)
        if ref_path.startswith(
            ("http://", "https://", "data:")
        ):
            continue

        resolved = (md_dir / ref_path).resolve()
        if resolved.exists():
            found.append(
                (match.group(0), alt_text, resolved)
            )
        else:
            missing.append(
                (match.group(0), alt_text, ref_path)
            )
            console.print(
                f"[yellow]Warning:[/yellow] "
                f"Image not found: {ref_path}"
            )
    return found, missing


def get_image_dimensions(
    path: Path,
) -> tuple[int, int]:
    """Get image width and height in pixels."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.size
    except Exception:
        return (800, 600)  # fallback


def upload_image_to_drive(
    service,
    image_path: Path,
) -> Optional[tuple[str, str]]:
    """Upload image to Drive and make it public.

    Returns (public_url, file_id) or None on failure.
    """
    from googleapiclient.http import MediaFileUpload

    ext = image_path.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
    }
    mimetype = mime_map.get(ext)
    if not mimetype:
        console.print(
            f"[yellow]Warning:[/yellow] Unsupported "
            f"format: {ext}. Skipping {image_path.name}"
        )
        return None

    media = MediaFileUpload(
        str(image_path), mimetype=mimetype, resumable=True
    )
    try:
        uploaded = (
            service.files()
            .create(
                body={"name": image_path.name},
                media_body=media,
                fields="id",
            )
            .execute()
        )
        fid = uploaded["id"]
        service.permissions().create(
            fileId=fid,
            body={"type": "anyone", "role": "reader"},
        ).execute()
        url = f"https://lh3.googleusercontent.com/d/{fid}"
        return (url, fid)
    except Exception as e:
        console.print(
            f"[yellow]Warning:[/yellow] Failed to upload "
            f"{image_path.name}: {e}"
        )
        return None


def preprocess_images(
    service,
    content: str,
    md_dir: Path,
) -> tuple[str, list[ImageInfo], list[str]]:
    """Upload images and replace refs with placeholders.

    Returns (modified_content, image_infos, file_ids).
    Images are uploaded at full resolution. Placeholders
    are inserted so the Docs API can later insert sized
    images at those positions.
    """
    found, missing = find_local_images(content, md_dir)

    for original_ref, alt_text, ref_path in missing:
        content = content.replace(
            original_ref, f"**[Image: {alt_text}]**", 1
        )
        console.print(
            f"  [dim]Stripped missing: {ref_path}[/dim]"
        )

    if not found:
        return content, [], []

    console.print(
        f"[cyan]Found {len(found)} local image(s) "
        f"to upload[/cyan]"
    )

    images: list[ImageInfo] = []
    file_ids: list[str] = []
    for i, (original_ref, alt_text, local_path) in enumerate(
        found
    ):
        console.print(
            f"  [dim]Uploading: {local_path.name}[/dim]"
        )
        result = upload_image_to_drive(service, local_path)
        if result:
            url, fid = result
            file_ids.append(fid)
            w, h = get_image_dimensions(local_path)
            placeholder = f"IMGPLACEHOLDER_{i}"
            images.append(
                ImageInfo(placeholder, url, fid, w, h)
            )
            content = content.replace(
                original_ref, placeholder, 1
            )
            console.print(
                f"  [green]Uploaded:[/green] "
                f"{local_path.name} ({w}x{h})"
            )
        else:
            content = content.replace(
                original_ref,
                f"**[Image: {alt_text}]**",
                1,
            )
            console.print(
                f"  [yellow]Skipped:[/yellow] "
                f"{local_path.name}"
            )

    return content, images, file_ids


def post_insert_images(
    docs_service,
    doc_id: str,
    images: list[ImageInfo],
    max_width_pt: float = MAX_IMAGE_WIDTH_PT,
) -> int:
    """Replace placeholders with sized images via Docs API.

    Reads the document, finds each placeholder, deletes it,
    and inserts the image at that position with display
    dimensions that fit within max_width_pt.

    Returns the number of images successfully inserted.
    """
    doc = (
        docs_service.documents()
        .get(documentId=doc_id)
        .execute()
    )

    # Find placeholder positions in document body
    placeholder_positions: list[
        tuple[int, int, ImageInfo]
    ] = []
    body = doc.get("body", {})
    for element in body.get("content", []):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        for pel in paragraph.get("elements", []):
            text_run = pel.get("textRun")
            if not text_run:
                continue
            text = text_run.get("content", "")
            start = pel.get("startIndex", 0)
            for img_info in images:
                idx = text.find(img_info.placeholder)
                if idx >= 0:
                    abs_start = start + idx
                    abs_end = abs_start + len(
                        img_info.placeholder
                    )
                    placeholder_positions.append(
                        (abs_start, abs_end, img_info)
                    )

    if not placeholder_positions:
        console.print(
            "[yellow]Warning:[/yellow] No placeholders "
            "found in document — images may not have "
            "been inserted."
        )
        return 0

    # Sort by position descending so deletions/insertions
    # at higher indices don't shift lower ones
    placeholder_positions.sort(
        key=lambda x: x[0], reverse=True
    )

    requests = []
    for abs_start, abs_end, img_info in placeholder_positions:
        # Calculate display size in points
        w_px = img_info.width_px
        h_px = img_info.height_px
        # Convert pixels to points (assume 96 DPI)
        w_pt = w_px * 0.75
        h_pt = h_px * 0.75
        if w_pt > max_width_pt:
            scale = max_width_pt / w_pt
            w_pt = max_width_pt
            h_pt = h_pt * scale

        # Delete placeholder text
        requests.append(
            {
                "deleteContentRange": {
                    "range": {
                        "startIndex": abs_start,
                        "endIndex": abs_end,
                    }
                }
            }
        )
        # Insert image at same position
        requests.append(
            {
                "insertInlineImage": {
                    "location": {"index": abs_start},
                    "uri": img_info.drive_url,
                    "objectSize": {
                        "width": {
                            "magnitude": w_pt,
                            "unit": "PT",
                        },
                        "height": {
                            "magnitude": h_pt,
                            "unit": "PT",
                        },
                    },
                }
            }
        )

    try:
        docs_service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()
        n = len(placeholder_positions)
        console.print(
            f"[green]Inserted {n} image(s) into "
            f"document[/green]"
        )
        return n
    except Exception as e:
        console.print(
            f"[red]Error inserting images:[/red] {e}"
        )
        return 0


def cleanup_temp_images(
    service, file_ids: list[str]
) -> None:
    """Delete temporary image files from Drive."""
    for fid in file_ids:
        try:
            service.files().delete(fileId=fid).execute()
        except Exception:
            pass


def upload_markdown(
    service,
    md_path: Path,
    folder_id: Optional[str],
    filename: str,
) -> Optional[tuple[str, str]]:
    """Upload markdown to Drive with native conversion.

    Returns (webViewLink, fileId) or None on failure.
    """
    from googleapiclient.http import MediaFileUpload

    file_metadata: dict = {
        "name": filename,
        "mimeType": "application/vnd.google-apps.document",
    }
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaFileUpload(
        str(md_path), mimetype="text/markdown",
        resumable=True,
    )
    try:
        with console.status(
            "[cyan]Uploading to Google Drive...[/cyan]"
        ):
            f = (
                service.files()
                .create(
                    body=file_metadata,
                    media_body=media,
                    fields="id,webViewLink",
                )
                .execute()
            )
        return (f.get("webViewLink"), f.get("id"))
    except Exception as e:
        console.print(f"[red]Upload error:[/red] {e}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Upload Markdown files to Google Drive "
            "as native Google Docs."
        ),
        formatter_class=(
            argparse.RawDescriptionHelpFormatter
        ),
        epilog="""
Examples:
  md2gdoc report.md
  md2gdoc report.md --folder "OTA/Reports"
  md2gdoc report.md --name "Q4 Summary"
  md2gdoc report.md --on-existing version
  md2gdoc report.md --max-image-width 5.0
        """,
    )

    parser.add_argument(
        "markdown_file",
        type=Path,
        help="Path to the Markdown file to upload",
    )
    parser.add_argument(
        "--folder", "-f", type=str, default="",
        help="Target folder (e.g., 'OTA/Reports')",
    )
    parser.add_argument(
        "--name", "-n", type=str, default="",
        help="Name for the Google Doc",
    )
    parser.add_argument(
        "--on-existing", type=str, default="ask",
        choices=["ask", "version", "overwrite"],
        help="Action when file exists (default: ask)",
    )
    parser.add_argument(
        "--no-images", action="store_true",
        help="Skip uploading local images",
    )
    parser.add_argument(
        "--max-image-width", type=float, default=6.5,
        help="Max image display width in inches "
        "(default: 6.5 = full page width)",
    )

    args = parser.parse_args()

    if not check_dependencies():
        sys.exit(1)

    if not args.markdown_file.exists():
        console.print(
            f"[red]Error:[/red] File not found: "
            f"{args.markdown_file}"
        )
        sys.exit(1)

    if args.markdown_file.suffix.lower() not in (
        ".md", ".markdown"
    ):
        console.print(
            "[yellow]Warning:[/yellow] File doesn't "
            "have .md extension, proceeding anyway"
        )

    service = get_drive_service()
    if not service:
        sys.exit(1)

    folder_id = None
    if args.folder:
        console.print(
            f"[dim]Finding folder: {args.folder}[/dim]"
        )
        folder_id = find_folder_id(service, args.folder)

    target_name = (
        args.name if args.name
        else args.markdown_file.stem
    )

    file_exists = check_file_exists(
        service, folder_id, target_name
    )
    final_name = target_name
    if file_exists:
        versioned_name = get_next_version_name(
            service, folder_id, target_name
        )
        on_existing = getattr(args, "on_existing", "ask")
        if on_existing == "ask":
            action = prompt_for_conflict(
                target_name, versioned_name
            )
            if action is None:
                console.print("[dim]Upload cancelled.[/dim]")
                sys.exit(0)
        else:
            action = on_existing

        if action == "version":
            final_name = versioned_name
            console.print(
                f"[dim]Using versioned name: "
                f"{final_name}[/dim]"
            )
        elif action == "overwrite":
            console.print(
                f"[dim]Deleting existing: "
                f"{target_name}[/dim]"
            )
            delete_file(service, folder_id, target_name)

    # --- Image preprocessing ---
    upload_path = args.markdown_file
    image_infos: list[ImageInfo] = []
    drive_file_ids: list[str] = []
    md_content = args.markdown_file.read_text(
        encoding="utf-8"
    )
    md_dir = args.markdown_file.parent.resolve()

    if args.no_images:
        processed = strip_local_image_refs(md_content)
    else:
        processed, image_infos, drive_file_ids = (
            preprocess_images(service, md_content, md_dir)
        )

    if processed != md_content:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False,
            encoding="utf-8",
        )
        tmp.write(processed)
        tmp.close()
        upload_path = Path(tmp.name)

    # --- Upload ---
    console.print(
        f"[cyan]Uploading[/cyan] "
        f"{args.markdown_file.name} → Google Docs..."
    )
    result = upload_markdown(
        service, upload_path, folder_id, final_name
    )

    if upload_path != args.markdown_file:
        try:
            upload_path.unlink()
        except OSError:
            pass

    if not result:
        cleanup_temp_images(service, drive_file_ids)
        sys.exit(1)

    web_link, doc_id = result

    # --- Post-insert images via Docs API ---
    n_images = 0
    if image_infos and doc_id:
        max_w_pt = args.max_image_width * 72  # in→pt
        docs_svc = get_docs_service()
        if docs_svc:
            console.print(
                "[cyan]Inserting images into "
                "document...[/cyan]"
            )
            n_images = post_insert_images(
                docs_svc, doc_id, image_infos,
                max_width_pt=max_w_pt,
            )
        else:
            console.print(
                "[yellow]Warning:[/yellow] Could not "
                "get Docs service — images left as "
                "placeholders."
            )

    # --- Cleanup ---
    if drive_file_ids:
        console.print(
            "[dim]Cleaning up temp images "
            "from Drive...[/dim]"
        )
        cleanup_temp_images(service, drive_file_ids)

    # --- Success ---
    location = (
        f"{args.folder}/{final_name}"
        if args.folder else final_name
    )
    img_info = ""
    if n_images:
        img_info = (
            f"\n[dim]Images:[/dim] {n_images} inserted"
        )

    console.print()
    console.print(
        Panel(
            f"[green]Successfully uploaded![/green]\n\n"
            f"[dim]Name:[/dim] {final_name}\n"
            f"[dim]Location:[/dim] {location}\n"
            f"[dim]Link:[/dim] {web_link}{img_info}",
            title="Done",
            border_style="green",
        )
    )


if __name__ == "__main__":
    main()
