#!/usr/bin/env python3
"""
gsheet2csv: Download Google Sheets as CSV files.

This tool uses the Google Drive API to export Google Sheets as CSV format.
Note: For multi-sheet spreadsheets, only the first sheet is exported.

Prerequisites:
- First run: Will open browser for OAuth authentication (one-time setup)
- Credentials stored in .gdoc-credentials.json (local) or ~/.config/md2gdoc/
"""

import argparse
import csv
import io
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

console = Console()

# Import shared utilities from md2gdoc
from claude_code_tools.md2gdoc import (
    check_dependencies,
    get_drive_service,
    find_folder_id,
)


def find_sheet_by_name(
    service, folder_id: Optional[str], sheet_name: str
) -> Optional[dict]:
    """Find a Google Sheet by name in a folder. Returns file metadata or None."""
    parent = folder_id if folder_id else "root"
    query = (
        f"name = '{sheet_name}' and "
        f"'{parent}' in parents and "
        f"mimeType = 'application/vnd.google-apps.spreadsheet' and "
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


def list_sheets_in_folder(service, folder_id: Optional[str]) -> list[dict]:
    """List all Google Sheets in a folder."""
    parent = folder_id if folder_id else "root"
    query = (
        f"'{parent}' in parents and "
        f"mimeType = 'application/vnd.google-apps.spreadsheet' and "
        f"trashed = false"
    )
    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name)",
            pageSize=100,
            orderBy="name",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return results.get("files", [])


def download_sheet_as_csv(service, file_id: str) -> Optional[str]:
    """Download a Google Sheet as CSV content.

    Note: Only exports the first sheet for multi-sheet spreadsheets.
    """
    try:
        content = (
            service.files()
            .export(fileId=file_id, mimeType="text/csv")
            .execute()
        )
        if isinstance(content, bytes):
            return content.decode("utf-8")
        return content
    except Exception as e:
        console.print(f"[red]Export error:[/red] {e}")
        return None


def get_sheets_service():
    """Get authenticated Google Sheets service."""
    from googleapiclient.discovery import build
    from claude_code_tools.md2gdoc import get_credentials

    creds = get_credentials()
    if not creds:
        return None
    return build("sheets", "v4", credentials=creds)


def list_sheet_tabs(service, file_id: str) -> list[dict]:
    """List all tabs (sheets) in a spreadsheet."""
    sheets_service = get_sheets_service()
    if not sheets_service:
        return []

    try:
        spreadsheet = (
            sheets_service.spreadsheets()
            .get(spreadsheetId=file_id)
            .execute()
        )
        sheets = spreadsheet.get("sheets", [])
        return [
            {
                "id": s["properties"]["sheetId"],
                "title": s["properties"]["title"],
                "index": s["properties"]["index"],
            }
            for s in sheets
        ]
    except Exception as e:
        console.print(f"[yellow]Warning:[/yellow] Could not list tabs: {e}")
        return []


def download_sheet_tab_as_csv(
    _drive_service, file_id: str, sheet_id: int
) -> Optional[str]:
    """Download a specific tab from a Google Sheet as CSV.

    Uses the export URL with gid parameter to specify the tab.
    """
    try:
        # Build export URL with specific sheet ID
        # This is how Google Sheets export works for specific tabs
        export_url = (
            f"https://docs.google.com/spreadsheets/d/{file_id}"
            f"/export?format=csv&gid={sheet_id}"
        )

        # Use authenticated session to fetch the specific tab
        from google.auth.transport.requests import AuthorizedSession
        from claude_code_tools.md2gdoc import get_credentials

        creds = get_credentials()
        if not creds:
            return None

        session = AuthorizedSession(creds)
        response = session.get(export_url)

        if response.status_code == 200:
            return response.text
        else:
            console.print(
                f"[red]Export error:[/red] HTTP {response.status_code}"
            )
            return None
    except Exception as e:
        console.print(f"[red]Export error:[/red] {e}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Google Sheets as CSV files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  gsheet2csv "My Spreadsheet"                      # Download from root
  gsheet2csv "My Spreadsheet" --folder "Reports"   # Download from folder
  gsheet2csv "My Spreadsheet" -o data.csv          # Save with custom name
  gsheet2csv "My Spreadsheet" -o data.tsv          # Save as TSV
  gsheet2csv "My Spreadsheet" --sheet "Sheet2"     # Download specific tab
  gsheet2csv --list --folder Reports               # List sheets in folder
  gsheet2csv "My Spreadsheet" --list-tabs          # List tabs in spreadsheet

Note: By default, exports the first sheet. Use --sheet to specify a tab name.
        """,
    )

    parser.add_argument(
        "sheet_name",
        type=str,
        nargs="?",
        help="Name of the Google Sheet to download",
    )

    parser.add_argument(
        "--folder", "-f",
        type=str,
        default="",
        help="Folder in Google Drive (e.g., 'Reports/Data')",
    )

    parser.add_argument(
        "--output", "-o",
        type=str,
        default="",
        help="Output filename (default: <sheet_name>.csv)",
    )

    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List Google Sheets in the folder instead of downloading",
    )

    parser.add_argument(
        "--list-tabs",
        action="store_true",
        help="List all tabs in the spreadsheet",
    )

    parser.add_argument(
        "--sheet", "-s",
        type=str,
        default="",
        help="Name of specific tab/sheet to export (default: first sheet)",
    )

    args = parser.parse_args()

    if not check_dependencies():
        sys.exit(1)

    # Need either sheet_name or --list
    if not args.sheet_name and not args.list:
        parser.print_help()
        sys.exit(1)

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
        sheets = list_sheets_in_folder(service, folder_id)
        if not sheets:
            console.print(
                "[yellow]No Google Sheets found in this folder.[/yellow]"
            )
            sys.exit(0)

        console.print(
            f"\n[bold]Spreadsheets in {args.folder or 'My Drive'}:[/bold]\n"
        )
        for sheet in sheets:
            console.print(f"  • {sheet['name']}")
        console.print(f"\n[dim]Total: {len(sheets)} spreadsheet(s)[/dim]")
        sys.exit(0)

    # Find the spreadsheet
    console.print(f"[dim]Looking for: {args.sheet_name}[/dim]")
    sheet = find_sheet_by_name(service, folder_id, args.sheet_name)

    if not sheet:
        console.print(
            f"[red]Error:[/red] Spreadsheet not found: {args.sheet_name}"
        )
        console.print("[dim]Use --list to see available spreadsheets[/dim]")
        sys.exit(1)

    # List tabs mode
    if args.list_tabs:
        tabs = list_sheet_tabs(service, sheet["id"])
        if not tabs:
            console.print("[yellow]Could not retrieve tab list.[/yellow]")
            sys.exit(1)

        console.print(f"\n[bold]Tabs in '{sheet['name']}':[/bold]\n")
        for tab in tabs:
            console.print(f"  {tab['index'] + 1}. {tab['title']}")
        console.print(f"\n[dim]Total: {len(tabs)} tab(s)[/dim]")
        sys.exit(0)

    # Download
    console.print(f"[cyan]Downloading[/cyan] {sheet['name']} → CSV...")

    # If specific tab requested, find it and use tab-specific export
    if args.sheet:
        tabs = list_sheet_tabs(service, sheet["id"])
        target_tab = None
        for tab in tabs:
            if tab["title"].lower() == args.sheet.lower():
                target_tab = tab
                break

        if not target_tab:
            console.print(
                f"[red]Error:[/red] Tab not found: {args.sheet}"
            )
            console.print("[dim]Use --list-tabs to see available tabs[/dim]")
            sys.exit(1)

        console.print(f"[dim]Exporting tab: {target_tab['title']}[/dim]")
        content = download_sheet_tab_as_csv(
            service, sheet["id"], target_tab["id"]
        )
    else:
        content = download_sheet_as_csv(service, sheet["id"])

    if content is None:
        sys.exit(1)

    # Determine output filename
    if args.output:
        output_path = Path(args.output)
    else:
        safe_name = "".join(
            c if c.isalnum() or c in "._- " else "_"
            for c in sheet["name"]
        )
        if args.sheet:
            safe_tab = "".join(
                c if c.isalnum() or c in "._- " else "_"
                for c in args.sheet
            )
            output_path = Path(f"{safe_name}_{safe_tab}.csv")
        else:
            output_path = Path(f"{safe_name}.csv")

    # Convert to TSV if output extension is .tsv
    if output_path.suffix.lower() == ".tsv":
        reader = csv.reader(io.StringIO(content))
        tsv_buf = io.StringIO()
        writer = csv.writer(tsv_buf, delimiter="\t")
        writer.writerows(reader)
        content = tsv_buf.getvalue()

    # Check if file exists
    if output_path.exists():
        console.print(
            f"[yellow]Warning:[/yellow] {output_path} exists, overwriting"
        )

    # Write file
    output_path.write_text(content, encoding="utf-8")

    tab_info = f" (tab: {args.sheet})" if args.sheet else ""

    console.print()
    console.print(
        Panel(
            f"[green]Successfully downloaded![/green]\n\n"
            f"[dim]Spreadsheet:[/dim] {sheet['name']}{tab_info}\n"
            f"[dim]Saved to:[/dim] {output_path}",
            title="Done",
            border_style="green",
        )
    )


if __name__ == "__main__":
    main()
