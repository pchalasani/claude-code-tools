#!/usr/bin/env python3
"""
gdoc2docx: Download Google Docs as Word (.docx) files.

Uses the Google Drive API to export Google Docs as DOCX,
and downloads non-Google-Doc files (DOCX, DOC, ODT) directly.

Prerequisites:
- First run: Will open browser for OAuth authentication
  (one-time setup)
- Credentials stored in .gdoc-credentials.json (local)
  or ~/.config/md2gdoc/
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

console = Console()

# Lazy imports — keep startup fast; these pull in Google API
# client libs which are heavy.
SCOPES = None
check_dependencies = None
get_drive_service = None
find_folder_id = None


def _ensure_imports() -> None:
    """Import shared helpers from md2gdoc on first use."""
    global SCOPES, check_dependencies, get_drive_service, find_folder_id
    if check_dependencies is not None:
        return
    from claude_code_tools.md2gdoc import (
        SCOPES as _SCOPES,
        check_dependencies as _check_dependencies,
        get_drive_service as _get_drive_service,
        find_folder_id as _find_folder_id,
    )
    SCOPES = _SCOPES
    check_dependencies = _check_dependencies
    get_drive_service = _get_drive_service
    find_folder_id = _find_folder_id


# Document types we can download as DOCX
CONVERTIBLE_TYPES = {
    "application/vnd.google-apps.document": "gdoc",
    "application/vnd.openxmlformats-officedocument"
    ".wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.oasis.opendocument.text": "odt",
}

DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument"
    ".wordprocessingml.document"
)


def find_doc_by_name(
    service,
    folder_id: Optional[str],
    doc_name: str,
) -> Optional[dict]:
    """Find a convertible document by name.

    Searches in the specified folder (or My Drive root if none).
    Falls back to searching all accessible files when no
    explicit folder was given.

    Returns:
        File metadata dict or None.
    """
    safe_name = doc_name.replace("'", "\\'")
    type_conditions = " or ".join(
        f"mimeType = '{mime}'" for mime in CONVERTIBLE_TYPES
    )

    parent = folder_id if folder_id else "root"
    query = (
        f"name = '{safe_name}' and "
        f"'{parent}' in parents and "
        f"({type_conditions}) and "
        f"trashed = false"
    )
    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name, mimeType)",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = results.get("files", [])
    if files:
        return files[0]

    # Broaden search when no explicit folder was given
    if not folder_id:
        broad_query = (
            f"name = '{safe_name}' and "
            f"({type_conditions}) and "
            f"trashed = false"
        )
        results = (
            service.files()
            .list(
                q=broad_query,
                fields="files(id, name, mimeType)",
                pageSize=1,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files = results.get("files", [])
        if files:
            return files[0]

    return None


def list_docs_in_folder(
    service,
    folder_id: Optional[str],
) -> list[dict]:
    """List all convertible documents in a folder."""
    parent = folder_id if folder_id else "root"
    type_conditions = " or ".join(
        f"mimeType = '{mime}'" for mime in CONVERTIBLE_TYPES
    )
    query = (
        f"'{parent}' in parents and "
        f"({type_conditions}) and "
        f"trashed = false"
    )
    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name, mimeType)",
            pageSize=100,
            orderBy="name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return results.get("files", [])


def download_doc_as_docx(
    service,
    file_id: str,
    mime_type: str,
) -> Optional[bytes]:
    """Download a document as DOCX bytes.

    For Google Docs, uses the export API. For other file types
    (DOCX, DOC, ODT), downloads the raw file content directly.

    Args:
        service: Google Drive API service instance.
        file_id: Google Drive file ID.
        mime_type: MIME type of the source file.

    Returns:
        DOCX file content as bytes, or None on error.
    """
    try:
        if mime_type == "application/vnd.google-apps.document":
            # Native Google Doc — export as DOCX
            return (
                service.files()
                .export(fileId=file_id, mimeType=DOCX_MIME)
                .execute()
            )
        elif mime_type == DOCX_MIME:
            # Already a DOCX — download directly
            return (
                service.files()
                .get_media(fileId=file_id)
                .execute()
            )
        else:
            # DOC or ODT — convert via temp Google Doc,
            # then export as DOCX
            return _convert_via_google_docs(
                service, file_id, mime_type
            )
    except Exception as e:
        console.print(f"[red]Export error:[/red] {e}")
        return None


def _convert_via_google_docs(
    service,
    file_id: str,
    mime_type: str,
) -> Optional[bytes]:
    """Convert DOC/ODT to DOCX via a temporary Google Doc.

    Creates a temporary Google Doc copy, exports as DOCX,
    then deletes the temporary copy.

    Args:
        service: Google Drive API service instance.
        file_id: Google Drive file ID.
        mime_type: MIME type of the source file.

    Returns:
        DOCX file content as bytes, or None on error.
    """
    temp_id = None
    try:
        # Get original filename
        original = (
            service.files()
            .get(fileId=file_id, fields="name")
            .execute()
        )
        temp_name = f"_temp_convert_{original.get('name', 'doc')}"

        # Copy as Google Doc (triggers conversion)
        copy_meta = {"name": temp_name}
        temp_doc = (
            service.files()
            .copy(
                fileId=file_id,
                body=copy_meta,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        temp_id = temp_doc["id"]

        # Export the Google Doc as DOCX
        content = (
            service.files()
            .export(fileId=temp_id, mimeType=DOCX_MIME)
            .execute()
        )
        return content

    except Exception as e:
        console.print(
            f"[red]Conversion error:[/red] {e}"
        )
        return None
    finally:
        # Clean up temp file
        if temp_id:
            try:
                service.files().delete(
                    fileId=temp_id,
                    supportsAllDrives=True,
                ).execute()
            except Exception:
                console.print(
                    "[yellow]Warning:[/yellow] "
                    "Could not delete temporary file"
                )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Google Docs as Word (.docx) files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  gdoc2docx "My Document"
  gdoc2docx "My Document" --folder "OTA/Reports"
  gdoc2docx "My Document" -o report.docx
  gdoc2docx --list --folder OTA

Credentials (in order of precedence):
  1. .gdoc-token.json in current directory (project-specific)
  2. ~/.config/md2gdoc/token.json (global)
  3. Application Default Credentials (gcloud)
        """,
    )

    parser.add_argument(
        "doc_name",
        type=str,
        nargs="?",
        help="Name of the Google Doc to download",
    )
    parser.add_argument(
        "--folder", "-f",
        type=str,
        default="",
        help="Folder in Google Drive (e.g., 'OTA/Reports')",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default="",
        help="Output filename (default: <doc_name>.docx)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List documents in the folder instead of "
        "downloading",
    )

    args = parser.parse_args()

    _ensure_imports()

    if not check_dependencies():
        sys.exit(1)

    if not args.doc_name and not args.list:
        parser.print_help()
        sys.exit(1)

    service = get_drive_service()
    if not service:
        sys.exit(1)

    # Resolve folder
    folder_id = None
    if args.folder:
        console.print(
            f"[dim]Finding folder: {args.folder}[/dim]"
        )
        folder_id = find_folder_id(
            service, args.folder, create_if_missing=False
        )
        if folder_id is None:
            console.print(
                f"[red]Error:[/red] Folder not found: "
                f"{args.folder}"
            )
            sys.exit(1)

    # List mode
    if args.list:
        docs = list_docs_in_folder(service, folder_id)
        if not docs:
            console.print(
                "[yellow]No convertible documents found "
                "in this folder.[/yellow]"
            )
            sys.exit(0)

        label = args.folder or "My Drive"
        console.print(
            f"\n[bold]Documents in {label}:[/bold]\n"
        )
        for doc in docs:
            doc_type = CONVERTIBLE_TYPES.get(
                doc.get("mimeType", ""), "unknown"
            )
            type_label = (
                f"[dim]({doc_type})[/dim]"
                if doc_type != "gdoc"
                else ""
            )
            console.print(f"  • {doc['name']} {type_label}")
        console.print(
            f"\n[dim]Total: {len(docs)} document(s)[/dim]"
        )
        sys.exit(0)

    # Download mode
    console.print(
        f"[dim]Looking for: {args.doc_name}[/dim]"
    )
    doc = find_doc_by_name(service, folder_id, args.doc_name)

    if not doc:
        console.print(
            f"[red]Error:[/red] Document not found: "
            f"{args.doc_name}"
        )
        console.print(
            "[dim]Use --list to see available documents[/dim]"
        )
        sys.exit(1)

    doc_type = CONVERTIBLE_TYPES.get(
        doc.get("mimeType", ""), "unknown"
    )
    console.print(
        f"[cyan]Downloading[/cyan] {doc['name']} "
        f"({doc_type}) → DOCX..."
    )
    content = download_doc_as_docx(
        service, doc["id"], doc.get("mimeType", "")
    )

    if content is None:
        sys.exit(1)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        safe_name = "".join(
            c if c.isalnum() or c in "._- " else "_"
            for c in doc["name"]
        )
        output_path = Path(f"{safe_name}.docx")

    if output_path.exists():
        console.print(
            f"[yellow]Warning:[/yellow] "
            f"{output_path} already exists, overwriting"
        )

    output_path.write_bytes(content)

    console.print()
    console.print(
        Panel(
            f"[green]Successfully downloaded![/green]\n\n"
            f"[dim]Document:[/dim] {doc['name']}\n"
            f"[dim]Saved to:[/dim] {output_path}",
            title="Done",
            border_style="green",
        )
    )


if __name__ == "__main__":
    main()
