#!/usr/bin/env python3
"""
gdoc2md: Download Google Docs as Markdown files.

This tool uses the Google Drive API to export Google Docs as Markdown,
using Google's native markdown export (same as File → Download → Markdown).

Prerequisites:
- First run: Will open browser for OAuth authentication (one-time setup)
- Credentials stored in .gdoc-credentials.json (local) or ~/.config/md2gdoc/
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

console = Console()


def strip_base64_images(content: str) -> str:
    """
    Replace base64-encoded images with placeholder text.

    Handles both:
    - Inline: ![alt](data:image/...;base64,...)
    - Reference-style (Google Docs format):
        ![][image1] in text
        [image1]: <data:image/...;base64,...> at end

    Replaces with blockquote placeholders visible in rendered markdown.
    """
    # Pattern 1: Inline base64 images
    inline_pattern = r'!\[([^\]]*)\]\(data:image/[^;]+;base64,[^)]+\)'

    counter = [0]

    def inline_replacer(match: re.Match) -> str:
        counter[0] += 1
        alt_text = match.group(1).strip()
        if alt_text:
            return f'\n> 🖼️ **IMAGE PLACEHOLDER:** {alt_text}\n'
        else:
            return f'\n> 🖼️ **IMAGE PLACEHOLDER:** image_{counter[0]:03d}\n'

    content = re.sub(inline_pattern, inline_replacer, content)

    # Pattern 2: Reference-style base64 image definitions at end of doc
    # Format: [image1]: <data:image/png;base64,...>
    ref_def_pattern = r'^\[([^\]]+)\]:\s*<data:image/[^;]+;base64,[^>]+>\s*$'

    # Find all base64 image reference names
    base64_refs = set(re.findall(ref_def_pattern, content, re.MULTILINE))

    # Remove the reference definitions
    content = re.sub(ref_def_pattern, '', content, flags=re.MULTILINE)

    # Replace usages of those references with placeholders
    # Format: ![][image1] or ![alt][image1]
    for ref_name in base64_refs:
        # Escape ref_name for regex
        escaped_ref = re.escape(ref_name)
        usage_pattern = rf'!\[([^\]]*)\]\[{escaped_ref}\]'

        def ref_replacer(match: re.Match) -> str:
            alt_text = match.group(1).strip()
            if alt_text:
                return f'\n> 🖼️ **IMAGE PLACEHOLDER:** {alt_text}\n'
            else:
                return f'\n> 🖼️ **IMAGE PLACEHOLDER:** {ref_name}\n'

        content = re.sub(usage_pattern, ref_replacer, content)

    # Clean up extra blank lines that may result from removals
    content = re.sub(r'\n{3,}', '\n\n', content)

    return content

# Import shared utilities from md2gdoc
from claude_code_tools.md2gdoc import (
    SCOPES,
    check_dependencies,
    get_credentials,
    get_drive_service,
    find_folder_id,
)


# Document types we can convert to markdown
CONVERTIBLE_TYPES = {
    "application/vnd.google-apps.document": "gdoc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/pdf": "pdf",
}


def find_doc_by_name(
    service, folder_id: Optional[str], doc_name: str
) -> Optional[dict]:
    """Find a convertible document by name in a folder. Returns file metadata or None."""
    parent = folder_id if folder_id else "root"

    # Search for any convertible document type
    type_conditions = " or ".join(
        f"mimeType = '{mime}'" for mime in CONVERTIBLE_TYPES.keys()
    )
    query = (
        f"name = '{doc_name}' and "
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
    return files[0] if files else None


def list_docs_in_folder(service, folder_id: Optional[str]) -> list[dict]:
    """List all convertible documents in a folder."""
    parent = folder_id if folder_id else "root"

    # Search for any convertible document type
    type_conditions = " or ".join(
        f"mimeType = '{mime}'" for mime in CONVERTIBLE_TYPES.keys()
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


def download_doc_as_markdown(service, file_id: str, mime_type: str) -> Optional[str]:
    """Download a document as Markdown content."""
    try:
        # For Google Docs, use export API directly
        if mime_type == "application/vnd.google-apps.document":
            content = (
                service.files()
                .export(fileId=file_id, mimeType="text/markdown")
                .execute()
            )
            if isinstance(content, bytes):
                return content.decode("utf-8")
            return content
        else:
            # For non-Google-Docs (PDF, DOCX, etc.), convert to Google Doc first
            # This is what happens when you click "Open in Google Docs" in the UI
            return convert_via_google_docs(service, file_id, mime_type)
    except Exception as e:
        console.print(f"[red]Export error:[/red] {e}")
        return None


def convert_via_google_docs(service, file_id: str, mime_type: str) -> Optional[str]:
    """Convert a file to Google Doc, export as markdown, then delete the temp copy."""
    try:
        # Get the original file name
        file_info = service.files().get(fileId=file_id, fields="name").execute()
        original_name = file_info.get("name", "temp")

        # Copy the file as a Google Doc (this triggers conversion)
        console.print("[dim]Converting via Google Docs...[/dim]")
        copy_metadata = {
            "name": f"_temp_convert_{original_name}",
            "mimeType": "application/vnd.google-apps.document",
        }
        copied_file = (
            service.files()
            .copy(fileId=file_id, body=copy_metadata, fields="id")
            .execute()
        )
        temp_doc_id = copied_file["id"]

        try:
            # Export the Google Doc as markdown
            content = (
                service.files()
                .export(fileId=temp_doc_id, mimeType="text/markdown")
                .execute()
            )

            if isinstance(content, bytes):
                content = content.decode("utf-8")

            return content
        finally:
            # Clean up: delete the temporary Google Doc
            console.print("[dim]Cleaning up temp file...[/dim]")
            try:
                service.files().delete(fileId=temp_doc_id).execute()
            except Exception:
                pass  # Best effort cleanup

    except Exception as e:
        console.print(f"[red]Conversion error:[/red] {e}")
        console.print("[dim]Falling back to pandoc...[/dim]")
        return download_and_convert_with_pandoc(service, file_id, mime_type)


def download_and_convert_with_pandoc(
    service, file_id: str, mime_type: str
) -> Optional[str]:
    """Download file and convert to markdown using pandoc."""
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("pandoc"):
        console.print(
            "[red]Error:[/red] pandoc required for non-Google-Docs files. "
            "Install with: brew install pandoc"
        )
        return None

    # Determine file extension
    ext_map = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/msword": ".doc",
        "application/vnd.oasis.opendocument.text": ".odt",
        "application/pdf": ".pdf",
    }
    ext = ext_map.get(mime_type, ".docx")

    try:
        # Download the file
        content = service.files().get_media(fileId=file_id).execute()

        # Save to temp file and convert
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        # Convert with pandoc
        result = subprocess.run(
            ["pandoc", tmp_path, "-t", "markdown", "-o", "-"],
            capture_output=True,
            text=True,
        )

        # Clean up
        import os
        os.unlink(tmp_path)

        if result.returncode != 0:
            console.print(f"[red]Pandoc error:[/red] {result.stderr}")
            return None

        return result.stdout
    except Exception as e:
        console.print(f"[red]Download/convert error:[/red] {e}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Google Docs as Markdown files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  gdoc2md "My Document"                        # Download from root
  gdoc2md "My Document" --folder "OTA/Reports" # Download from folder
  gdoc2md "My Document" -o report.md           # Save with custom name
  gdoc2md "My Document" --images               # Keep base64 images
  gdoc2md --list --folder OTA                  # List docs in folder

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
        "--folder",
        "-f",
        type=str,
        default="",
        help="Folder in Google Drive (e.g., 'OTA/Reports')",
    )

    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="",
        help="Output filename (default: <doc_name>.md)",
    )

    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List Google Docs in the folder instead of downloading",
    )

    parser.add_argument(
        "--images",
        action="store_true",
        help="Keep base64-encoded images (default: replace with placeholders)",
    )

    args = parser.parse_args()

    # Check dependencies
    if not check_dependencies():
        sys.exit(1)

    # Need either doc_name or --list
    if not args.doc_name and not args.list:
        parser.print_help()
        sys.exit(1)

    # Get Drive service
    service = get_drive_service()
    if not service:
        sys.exit(1)

    # Find folder if specified
    folder_id = None
    if args.folder:
        console.print(f"[dim]Finding folder: {args.folder}[/dim]")
        folder_id = find_folder_id(service, args.folder, create_if_missing=False)
        if folder_id is None:
            console.print(f"[red]Error:[/red] Folder not found: {args.folder}")
            sys.exit(1)

    # List mode
    if args.list:
        docs = list_docs_in_folder(service, folder_id)
        if not docs:
            console.print("[yellow]No convertible documents found in this folder.[/yellow]")
            sys.exit(0)

        console.print(f"\n[bold]Documents in {args.folder or 'My Drive'}:[/bold]\n")
        for doc in docs:
            doc_type = CONVERTIBLE_TYPES.get(doc.get('mimeType', ''), 'unknown')
            type_label = f"[dim]({doc_type})[/dim]" if doc_type != "gdoc" else ""
            console.print(f"  • {doc['name']} {type_label}")
        console.print(f"\n[dim]Total: {len(docs)} document(s)[/dim]")
        sys.exit(0)

    # Download mode
    console.print(f"[dim]Looking for: {args.doc_name}[/dim]")
    doc = find_doc_by_name(service, folder_id, args.doc_name)

    if not doc:
        console.print(f"[red]Error:[/red] Document not found: {args.doc_name}")
        # Suggest listing
        console.print(f"[dim]Use --list to see available documents[/dim]")
        sys.exit(1)

    # Download as markdown
    doc_type = CONVERTIBLE_TYPES.get(doc.get('mimeType', ''), 'unknown')
    console.print(f"[cyan]Downloading[/cyan] {doc['name']} ({doc_type}) → Markdown...")
    content = download_doc_as_markdown(service, doc["id"], doc.get("mimeType", ""))

    if content is None:
        sys.exit(1)

    # Strip base64 images unless --images flag is used
    if not args.images:
        content = strip_base64_images(content)

    # Determine output filename
    if args.output:
        output_path = Path(args.output)
    else:
        # Use doc name, sanitize for filesystem
        safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in doc["name"])
        output_path = Path(f"{safe_name}.md")

    # Check if file exists
    if output_path.exists():
        console.print(
            f"[yellow]Warning:[/yellow] {output_path} already exists, overwriting"
        )

    # Write file
    output_path.write_text(content, encoding="utf-8")

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
