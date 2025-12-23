"""Tantivy-based search index for session exports."""

import json
import math
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import version as get_pkg_version
from pathlib import Path
from typing import Any, Optional

from claude_code_tools.session_utils import is_valid_session


def _get_package_version() -> str:
    """Get installed package version for automatic index rebuilding."""
    try:
        return get_pkg_version("claude-code-tools")
    except Exception:
        return "unknown"

# Lazy imports to allow module to load even if deps not installed
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    yaml = None  # type: ignore
    YAML_AVAILABLE = False

try:
    import tantivy
    TANTIVY_AVAILABLE = True
except ImportError:
    tantivy = None  # type: ignore
    TANTIVY_AVAILABLE = False


def _require_deps():
    """Raise helpful error if required dependencies are not installed."""
    missing = []
    if not YAML_AVAILABLE:
        missing.append("pyyaml")
    if not TANTIVY_AVAILABLE:
        missing.append("tantivy")

    if missing:
        raise ImportError(
            f"Missing dependencies for search indexing: {', '.join(missing)}\n"
            f"Install with: pip install {' '.join(missing)}\n"
            "Or reinstall claude-code-tools: uv tool install claude-code-tools"
        )


def _iso_to_epoch_ms(iso_str: str) -> int:
    """Convert ISO timestamp string to epoch milliseconds.

    Args:
        iso_str: ISO format timestamp (e.g., "2025-12-08T18:22:59.582Z")

    Returns:
        Epoch time in milliseconds, or 0 if parsing fails
    """
    if not iso_str:
        return 0
    try:
        # Handle both Z suffix and +00:00 timezone formats
        if "T" in iso_str:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(iso_str)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


@dataclass
class SearchResult:
    """A search result with metadata and snippet."""

    session_id: str
    agent: str
    project: str
    branch: Optional[str]
    cwd: Optional[str]
    modified: str
    lines: int
    snippet: str
    score: float
    export_path: str


class IndexState:
    """Track which files have been indexed to enable incremental updates."""

    def __init__(self, state_path: Path):
        self.state_path = state_path
        self.indexed_files: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self):
        """Load state from disk."""
        if self.state_path.exists():
            try:
                with open(self.state_path, "r") as f:
                    self.indexed_files = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.indexed_files = {}

    def save(self):
        """Save state to disk."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(self.indexed_files, f)

    def needs_reindex(self, file_path: Path) -> bool:
        """Check if file needs to be (re-)indexed."""
        key = str(file_path)
        if key not in self.indexed_files:
            return True

        stat = file_path.stat()
        stored = self.indexed_files[key]

        # Reindex if mtime or size changed
        return (
            stat.st_mtime != stored.get("mtime")
            or stat.st_size != stored.get("size")
        )

    def mark_indexed(self, file_path: Path):
        """Mark file as indexed with current metadata."""
        stat = file_path.stat()
        self.indexed_files[str(file_path)] = {
            "mtime": stat.st_mtime,
            "size": stat.st_size,
        }


class SessionIndex:
    """Tantivy-based full-text search index for sessions."""

    def __init__(self, index_path: Path):
        """
        Initialize or open a session index.

        Args:
            index_path: Directory for the Tantivy index

        Raises:
            ImportError: If tantivy or pyyaml is not installed
        """
        _require_deps()

        self.index_path = Path(index_path)
        self.state = IndexState(self.index_path / "index_state.json")

        # Define schema
        self.schema_builder = tantivy.SchemaBuilder()

        # Stored fields (retrieved but not searched)
        self.schema_builder.add_text_field("session_id", stored=True)
        self.schema_builder.add_text_field("agent", stored=True)
        self.schema_builder.add_text_field("project", stored=True)
        self.schema_builder.add_text_field("branch", stored=True)
        self.schema_builder.add_text_field("cwd", stored=True)
        self.schema_builder.add_text_field("created", stored=True)
        self.schema_builder.add_text_field("modified", stored=True)
        # Fast field for sorting by modification time (epoch milliseconds)
        self.schema_builder.add_unsigned_field("modified_ts", stored=True, fast=True)
        self.schema_builder.add_integer_field("lines", stored=True)
        # export_path needs "raw" tokenizer for exact match deletion
        self.schema_builder.add_text_field("export_path", stored=True, tokenizer_name="raw")

        # First and last message fields (for preview in TUI)
        self.schema_builder.add_text_field("first_msg_role", stored=True)
        self.schema_builder.add_text_field("first_msg_content", stored=True)
        self.schema_builder.add_text_field("last_msg_role", stored=True)
        self.schema_builder.add_text_field("last_msg_content", stored=True)

        # Session type fields (for filtering in TUI)
        self.schema_builder.add_text_field("derivation_type", stored=True)
        self.schema_builder.add_text_field("is_sidechain", stored=True)  # "true"/"false"

        # Claude home field (for filtering by source Claude home directory)
        # Use "raw" tokenizer so paths are indexed as single tokens for exact matching
        self.schema_builder.add_text_field("claude_home", stored=True, tokenizer_name="raw")

        # Custom title field (from /rename command)
        self.schema_builder.add_text_field("custom_title", stored=True)

        # Searchable content field
        self.schema_builder.add_text_field("content", stored=True)

        self.schema = self.schema_builder.build()

        # Create or open index
        self.index_path.mkdir(parents=True, exist_ok=True)

        # Check index version - rebuild if package version changed
        version_file = self.index_path / "VERSION"
        current_version = _get_package_version()
        needs_rebuild = False

        if version_file.exists():
            try:
                stored_version = version_file.read_text().strip()
                if stored_version != current_version:
                    needs_rebuild = True
                    print(
                        f"Package version changed ({stored_version} -> {current_version}), "
                        "rebuilding index..."
                    )
            except IOError:
                needs_rebuild = True
                print("Index version file corrupted, rebuilding...")
        elif (self.index_path / "meta.json").exists():
            # Index exists but no VERSION file - legacy index, rebuild
            needs_rebuild = True
            print("Upgrading index to versioned format, rebuilding...")

        if needs_rebuild:
            # Clear index directory contents (but keep the directory itself)
            for item in self.index_path.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            # Reset state tracker
            self.state = IndexState(self.index_path / "index_state.json")

        # Create or open index
        self.index = tantivy.Index(self.schema, path=str(self.index_path))

        # Write current version
        version_file.write_text(current_version)

    def _parse_export_file(self, export_path: Path) -> Optional[dict[str, Any]]:
        """
        Parse an exported session file with YAML front matter.

        Returns:
            Dict with metadata and content, or None if parsing fails
        """
        try:
            content = export_path.read_text(encoding="utf-8")

            if not content.startswith("---\n"):
                return None

            end_idx = content.find("\n---\n", 4)
            if end_idx == -1:
                return None

            yaml_str = content[4:end_idx]
            metadata = yaml.safe_load(yaml_str)

            conversation = content[end_idx + 5:]

            return {
                "metadata": metadata,
                "content": conversation,
                "export_path": str(export_path),
            }
        except Exception:
            return None

    def build_from_exports(
        self, exports_dir: Path, incremental: bool = True
    ) -> dict[str, int]:
        """
        Build or update index from exported session files.

        Args:
            exports_dir: Directory containing exported .txt files
                (expects subdirs: claude/, codex/)
            incremental: If True, only index new/modified files

        Returns:
            Stats dict: {indexed, skipped, failed}
        """
        stats = {"indexed": 0, "skipped": 0, "failed": 0}

        writer = self.index.writer()

        # Find all export files
        export_files = []
        for subdir in ["claude", "codex"]:
            subdir_path = exports_dir / subdir
            if subdir_path.exists():
                export_files.extend(subdir_path.glob("*.txt"))

        for export_path in export_files:
            # Check if needs indexing
            if incremental and not self.state.needs_reindex(export_path):
                stats["skipped"] += 1
                continue

            # Parse export file
            parsed = self._parse_export_file(export_path)
            if parsed is None:
                stats["failed"] += 1
                continue

            metadata = parsed["metadata"]

            # Create document
            doc = tantivy.Document()
            doc.add_text("session_id", metadata.get("session_id", ""))
            doc.add_text("agent", metadata.get("agent", ""))
            doc.add_text("project", metadata.get("project", ""))
            doc.add_text("branch", metadata.get("branch", "") or "")
            doc.add_text("cwd", metadata.get("cwd", "") or "")
            doc.add_text("created", metadata.get("created", "") or "")
            modified_str = metadata.get("modified", "")
            doc.add_text("modified", modified_str)
            doc.add_unsigned("modified_ts", _iso_to_epoch_ms(modified_str))
            doc.add_integer("lines", metadata.get("lines", 0))
            doc.add_text("export_path", parsed["export_path"])

            # First and last message fields
            first_msg = metadata.get("first_msg", {}) or {}
            last_msg = metadata.get("last_msg", {}) or {}
            doc.add_text("first_msg_role", first_msg.get("role", ""))
            doc.add_text("first_msg_content", first_msg.get("content", ""))
            doc.add_text("last_msg_role", last_msg.get("role", ""))
            doc.add_text("last_msg_content", last_msg.get("content", ""))

            # Session type fields
            doc.add_text("derivation_type", metadata.get("derivation_type", "") or "")
            doc.add_text(
                "is_sidechain",
                "true" if metadata.get("is_sidechain") else "false"
            )

            # Custom title (from /rename command)
            doc.add_text("custom_title", metadata.get("customTitle", "") or "")

            doc.add_text("content", parsed["content"])

            writer.add_document(doc)
            self.state.mark_indexed(export_path)
            stats["indexed"] += 1

        writer.commit()
        self.state.save()
        self.index.reload()

        return stats

    def index_single_file(
        self, export_path: Path, writer, incremental: bool = True
    ) -> dict:
        """
        Index a single export file.

        Args:
            export_path: Path to exported .txt file
            writer: Tantivy index writer
            incremental: If True, skip if already indexed

        Returns:
            Dict with result: {status, error}
            status: 'indexed', 'skipped', 'failed'
        """
        result: dict = {"status": "failed", "error": None}

        if not export_path.exists():
            result["error"] = "file not found"
            return result

        # Check if needs indexing
        if incremental and not self.state.needs_reindex(export_path):
            result["status"] = "skipped"
            return result

        # Parse export file
        try:
            parsed = self._parse_export_file(export_path)
        except Exception as e:
            result["error"] = f"parse error: {e}"
            return result

        if parsed is None:
            result["error"] = "failed to parse YAML frontmatter"
            return result

        metadata = parsed["metadata"]

        try:
            # Create document
            doc = tantivy.Document()
            doc.add_text("session_id", metadata.get("session_id", ""))
            doc.add_text("agent", metadata.get("agent", ""))
            doc.add_text("project", metadata.get("project", ""))
            doc.add_text("branch", metadata.get("branch", "") or "")
            doc.add_text("cwd", metadata.get("cwd", "") or "")
            doc.add_text("created", metadata.get("created", "") or "")
            modified_str = metadata.get("modified", "")
            doc.add_text("modified", modified_str)
            doc.add_unsigned("modified_ts", _iso_to_epoch_ms(modified_str))
            doc.add_integer("lines", metadata.get("lines", 0))
            doc.add_text("export_path", parsed["export_path"])

            # First and last message fields
            first_msg = metadata.get("first_msg", {}) or {}
            last_msg = metadata.get("last_msg", {}) or {}
            doc.add_text("first_msg_role", first_msg.get("role", ""))
            doc.add_text("first_msg_content", first_msg.get("content", ""))
            doc.add_text("last_msg_role", last_msg.get("role", ""))
            doc.add_text("last_msg_content", last_msg.get("content", ""))

            # Session type fields
            doc.add_text(
                "derivation_type", metadata.get("derivation_type", "") or ""
            )
            doc.add_text(
                "is_sidechain",
                "true" if metadata.get("is_sidechain") else "false"
            )

            # Custom title (from /rename command)
            doc.add_text("custom_title", metadata.get("customTitle", "") or "")

            doc.add_text("content", parsed["content"])

            writer.add_document(doc)
            self.state.mark_indexed(export_path)
            result["status"] = "indexed"
        except Exception as e:
            result["error"] = f"index error: {e}"

        return result

    def get_writer(self):
        """Get a writer for batch indexing."""
        return self.index.writer()

    def commit_and_reload(self, writer):
        """Commit writer changes and reload index."""
        writer.commit()
        self.state.save()
        self.index.reload()

    def build_from_files(
        self, export_files: list[Path], incremental: bool = True
    ) -> dict[str, int]:
        """
        Build or update index from a list of exported session files.

        This is the per-project alternative to build_from_exports().
        Instead of expecting a single directory, it accepts a list of
        export file paths from multiple project directories.

        Args:
            export_files: List of paths to exported .txt files
            incremental: If True, only index new/modified files

        Returns:
            Stats dict: {indexed, skipped, failed}
        """
        stats = {"indexed": 0, "skipped": 0, "failed": 0}

        writer = self.get_writer()

        for export_path in export_files:
            result = self.index_single_file(export_path, writer, incremental)
            if result["status"] == "indexed":
                stats["indexed"] += 1
            elif result["status"] == "skipped":
                stats["skipped"] += 1
            else:
                stats["failed"] += 1

        self.commit_and_reload(writer)

        return stats

    def _extract_session_content(
        self, jsonl_path: Path, agent: str
    ) -> tuple[str, int, str]:
        """
        Extract searchable content from a session file.

        Handles both Claude and Codex JSONL formats.

        Args:
            jsonl_path: Path to session JSONL file
            agent: Agent type ('claude' or 'codex')

        Returns:
            Tuple of (content_string, user_message_count, custom_title)
        """
        messages = []
        user_count = 0  # Count only user messages for the "lines" metric
        custom_title = ""  # Session name from /rename command

        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Check for custom-title (from /rename command)
                    if data.get("type") == "custom-title":
                        custom_title = data.get("customTitle", "")
                        continue

                    role: Optional[str] = None
                    text: str = ""

                    if agent == "claude":
                        # Claude format: type is "user" or "assistant"
                        msg_type = data.get("type")
                        if msg_type not in ("user", "assistant"):
                            continue

                        role = msg_type
                        message = data.get("message", {})
                        content = message.get("content")

                        # Count user messages, but exclude tool results
                        # Tool results have content as list with {"type": "tool_result"}
                        # Real user messages have content as string or list with text
                        if role == "user":
                            is_tool_result = (
                                isinstance(content, list)
                                and len(content) > 0
                                and isinstance(content[0], dict)
                                and content[0].get("type") == "tool_result"
                            )
                            if not is_tool_result:
                                user_count += 1

                        if not content:
                            continue

                        # Extract text content
                        if isinstance(content, str):
                            text = content
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, str):
                                    text += block + "\n"
                                elif isinstance(block, dict):
                                    if block.get("type") == "text":
                                        text += block.get("text", "") + "\n"
                                    elif block.get("type") == "tool_use":
                                        tool_name = block.get("name", "")
                                        text += f"[Tool: {tool_name}]\n"
                                    elif block.get("type") == "tool_result":
                                        result = block.get("content", "")
                                        if isinstance(result, str):
                                            text += result[:500] + "\n"

                    elif agent == "codex":
                        # Codex format: type is "response_item" with payload
                        if data.get("type") != "response_item":
                            continue

                        payload = data.get("payload", {})
                        if payload.get("type") != "message":
                            continue

                        role = payload.get("role")
                        if role == "user":
                            user_count += 1
                        content = payload.get("content", [])

                        if not isinstance(content, list):
                            continue

                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            block_type = block.get("type")
                            # input_text (user) and output_text (assistant)
                            if block_type in ("input_text", "output_text"):
                                text += block.get("text", "") + "\n"

                    if role and text.strip():
                        messages.append(f"[{role}] {text.strip()}")

        except (OSError, IOError):
            pass

        return "\n\n".join(messages), user_count, custom_title

    def _parse_jsonl_session(self, jsonl_path: Path) -> Optional[dict[str, Any]]:
        """
        Parse a JSONL session file directly (no export step).

        Uses extract_session_metadata() from export_session.py for metadata,
        and handles both Claude and Codex JSONL formats for content.

        Args:
            jsonl_path: Path to the session JSONL file

        Returns:
            Dict with metadata and content suitable for indexing, or None on failure
        """
        try:
            # Detect agent from path
            path_str = str(jsonl_path)
            agent = "codex" if ".codex" in path_str else "claude"

            # Use existing helper for metadata extraction
            from claude_code_tools.export_session import extract_session_metadata
            metadata = extract_session_metadata(jsonl_path, agent)

            # Extract content for full-text search (also extracts custom_title)
            content, msg_count, custom_title = self._extract_session_content(
                jsonl_path, agent
            )

            # Add custom_title to metadata (extracted during content scan)
            if custom_title:
                metadata["customTitle"] = custom_title

            if msg_count == 0:
                return {"_skip_reason": "empty"}

            # Map metadata fields to expected format
            first_msg = metadata.get("first_msg") or {"role": "", "content": ""}
            last_msg = metadata.get("last_msg") or {"role": "", "content": ""}

            # Always use filename-derived session_id (the canonical identifier)
            # Internal sessionId field can be stale in forked sessions
            # Extract UUID from filename: last 36 chars of stem (handles both
            # "uuid.jsonl" and "rollout-timestamp-uuid.jsonl" formats)
            stem = jsonl_path.stem
            session_id = stem[-36:] if len(stem) >= 36 else stem

            return {
                "metadata": {
                    "session_id": session_id,
                    "agent": agent,
                    "project": metadata.get("project", ""),
                    "branch": metadata.get("branch", "") or "",
                    "cwd": metadata.get("cwd", "") or "",
                    "created": metadata.get("created", "") or "",
                    "modified": metadata.get("modified", "") or "",
                    "is_sidechain": metadata.get("is_sidechain", False),
                    "derivation_type": metadata.get("derivation_type", "") or "",
                    "session_type": metadata.get("session_type"),
                    "customTitle": metadata.get("customTitle", "") or "",
                },
                "content": content,
                "first_msg": first_msg,
                "last_msg": last_msg,
                "lines": msg_count,
                "file_path": str(jsonl_path),
            }
        except Exception as e:
            return {"_skip_reason": "parse_error", "_error": str(e)}

    def index_from_jsonl(
        self,
        jsonl_files: list[Path],
        incremental: bool = True,
        claude_home: Optional[Path] = None,
        codex_home: Optional[Path] = None,
        show_progress: bool = False,
    ) -> dict[str, int]:
        """
        Build or update index directly from JSONL session files.

        This is the "zero-config" approach - no export step needed.
        Parses JSONL directly and indexes to Tantivy.

        Args:
            jsonl_files: List of paths to session JSONL files
            incremental: If True, only index new/modified files
            claude_home: Claude home directory (stored for filtering)
            codex_home: Codex home directory (stored for filtering)
            show_progress: If True, show tqdm progress bar

        Returns:
            Stats dict: {indexed, skipped, failed}
        """
        claude_home_str = str(claude_home) if claude_home else ""
        codex_home_str = str(codex_home) if codex_home else str(Path.home() / ".codex")
        stats = {
            "indexed": 0, "skipped": 0, "failed": 0,
            "empty": 0, "parse_error": 0, "index_error": 0,
        }

        writer = self.get_writer()

        # Wrap iterator with tqdm if progress requested
        file_iter: Any = jsonl_files
        if show_progress:
            try:
                from tqdm import tqdm
                file_iter = tqdm(
                    jsonl_files,
                    desc="Indexing sessions",
                    unit="file",
                    file=sys.stderr,
                )
            except ImportError:
                print("Note: Install tqdm for progress bar (pip install tqdm)",
                      file=sys.stderr)

        for jsonl_path in file_iter:
            # Check if needs indexing
            if incremental and not self.state.needs_reindex(jsonl_path):
                stats["skipped"] += 1
                continue

            # Skip invalid sessions (metadata-only files like file-history-snapshot)
            if not is_valid_session(jsonl_path):
                stats["skipped"] += 1
                # Mark as indexed so we don't re-check next time
                self.state.mark_indexed(jsonl_path)
                continue

            # Parse JSONL file
            parsed = self._parse_jsonl_session(jsonl_path)
            if parsed is None or "_skip_reason" in parsed:
                stats["failed"] += 1
                if parsed:
                    reason = parsed.get("_skip_reason", "unknown")
                    if reason == "empty":
                        stats["empty"] += 1
                    elif reason == "parse_error":
                        stats["parse_error"] += 1
                continue

            metadata = parsed["metadata"]
            first_msg = parsed["first_msg"]
            last_msg = parsed["last_msg"]

            # Skip helper sessions (SDK/headless sessions used for analysis)
            if metadata.get("session_type") == "helper":
                stats["skipped"] += 1
                # Mark as indexed so we don't re-check next time
                self.state.mark_indexed(jsonl_path)
                continue

            # Skip sessions run from inside claude_home or codex_home directories
            cwd = metadata.get("cwd", "") or ""
            if cwd and (
                (claude_home_str and cwd.startswith(claude_home_str))
                or (codex_home_str and cwd.startswith(codex_home_str))
            ):
                stats["skipped"] += 1
                continue

            try:
                session_id = metadata.get("session_id", "")
                file_path_str = str(jsonl_path)

                # Delete by file path (unique), not sessionId (shared by sub-agents)
                writer.delete_documents("export_path", file_path_str)

                # Create document
                doc = tantivy.Document()
                doc.add_text("session_id", session_id)
                doc.add_text("agent", metadata.get("agent", ""))
                doc.add_text("project", metadata.get("project", ""))
                doc.add_text("branch", metadata.get("branch", "") or "")
                doc.add_text("cwd", metadata.get("cwd", "") or "")
                doc.add_text("created", metadata.get("created", "") or "")
                modified_str = metadata.get("modified", "")
                doc.add_text("modified", modified_str)
                doc.add_unsigned("modified_ts", _iso_to_epoch_ms(modified_str))
                doc.add_integer("lines", parsed.get("lines", 0))
                doc.add_text("export_path", parsed["file_path"])  # Store JSONL path

                # First and last message fields
                doc.add_text("first_msg_role", first_msg.get("role", ""))
                doc.add_text("first_msg_content", first_msg.get("content", ""))
                doc.add_text("last_msg_role", last_msg.get("role", ""))
                doc.add_text("last_msg_content", last_msg.get("content", ""))

                # Session type fields
                doc.add_text(
                    "derivation_type", metadata.get("derivation_type", "") or ""
                )
                doc.add_text(
                    "is_sidechain",
                    "true" if metadata.get("is_sidechain") else "false"
                )

                # Custom title (from /rename command)
                doc.add_text("custom_title", metadata.get("customTitle", "") or "")

                # Source home (for filtering by source directory)
                # Detect from path whether this is a Claude or Codex session
                agent = metadata.get("agent", "")
                if agent == "codex" or ".codex" in file_path_str:
                    # Codex session - store codex home
                    doc.add_text("claude_home", codex_home_str)
                else:
                    # Claude session - store claude home
                    doc.add_text("claude_home", claude_home_str)

                doc.add_text("content", parsed["content"])

                writer.add_document(doc)
                self.state.mark_indexed(jsonl_path)
                stats["indexed"] += 1
            except Exception:
                stats["failed"] += 1
                stats["index_error"] += 1

        self.commit_and_reload(writer)

        return stats

    def prune_deleted(self) -> int:
        """
        Remove deleted files from the index.

        Checks all files tracked in the index state and removes any that
        no longer exist on disk. This handles the case where sessions are
        deleted outside of the search tool.

        Returns:
            Number of entries pruned from the index.
        """
        if not self.state.indexed_files:
            return 0

        # First pass: identify deleted files
        files_to_remove = [
            file_path for file_path in self.state.indexed_files
            if not Path(file_path).exists()
        ]

        if not files_to_remove:
            return 0

        # Only create writer if we have deletions
        writer = self.get_writer()

        for file_path in files_to_remove:
            # Delete from Tantivy index
            writer.delete_documents("export_path", file_path)
            # Remove from state tracking
            del self.state.indexed_files[file_path]

        self.commit_and_reload(writer)

        return len(files_to_remove)

    def _generate_snippet(
        self, content: str, query: str, max_len: int = 200
    ) -> str:
        """
        Generate a snippet centered around the first match.

        Args:
            content: Full content text
            query: Search query
            max_len: Maximum snippet length

        Returns:
            Snippet with match context
        """
        if not query:
            # For empty query, return start of content
            return content[:max_len].strip() + "..." if len(content) > max_len else content.strip()

        lower_content = content.lower()
        lower_query = query.lower()

        # Find first occurrence
        pos = lower_content.find(lower_query)
        if pos == -1:
            # Try individual words
            for word in query.split():
                pos = lower_content.find(word.lower())
                if pos != -1:
                    break

        if pos == -1:
            return content[:max_len].strip() + "..."

        # Center snippet around match
        half_len = max_len // 2
        start = max(0, pos - half_len)
        end = min(len(content), pos + len(query) + half_len)

        snippet = content[start:end].strip()

        # Add ellipses
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."

        # Clean up newlines
        snippet = " ".join(snippet.split())

        return snippet

    def _calculate_recency_score(
        self, modified: str, base_score: float, half_life_days: float = 7.0
    ) -> float:
        """
        Apply recency boost using exponential decay.

        Args:
            modified: ISO format timestamp
            base_score: Original Tantivy score
            half_life_days: Days for score to decay by half

        Returns:
            Score with recency boost (up to 2x for very recent)
        """
        try:
            # Parse modified timestamp
            if "T" in modified:
                mod_time = datetime.fromisoformat(modified.replace("Z", "+00:00"))
            else:
                mod_time = datetime.fromisoformat(modified)

            now = datetime.now(mod_time.tzinfo) if mod_time.tzinfo else datetime.now()
            age_seconds = (now - mod_time).total_seconds()

            half_life_secs = half_life_days * 24 * 3600
            recency_boost = 1.0 + math.exp(-age_seconds / half_life_secs)

            return base_score * recency_boost
        except Exception:
            return base_score

    def search(
        self,
        query: str,
        limit: int = 50,
        project: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Search the index.

        Args:
            query: Search query (empty returns recent sessions)
            limit: Maximum results
            project: Filter to specific project

        Returns:
            List of result dicts with metadata and snippets
        """
        if not query and not project:
            return self.get_recent(limit=limit)

        searcher = self.index.searcher()

        # Build query
        if query:
            # Use Index.parse_query with default fields
            parsed_query = self.index.parse_query(query, ["content"])
        else:
            # Match all for project filter
            parsed_query = self.index.parse_query("*", ["content"])

        # Execute search
        results = []
        top_docs = searcher.search(parsed_query, limit * 2)  # Fetch extra for filtering

        for score, doc_address in top_docs.hits:
            doc = searcher.doc(doc_address)

            # Extract fields
            session_id = doc.get_first("session_id")
            agent = doc.get_first("agent")
            doc_project = doc.get_first("project")
            branch = doc.get_first("branch")
            cwd = doc.get_first("cwd")
            created = doc.get_first("created")
            modified = doc.get_first("modified")
            lines = doc.get_first("lines")
            export_path = doc.get_first("export_path")
            content = doc.get_first("content")

            # Apply project filter
            if project and doc_project != project:
                continue

            # Skip if session file no longer exists (deleted outside of search)
            if export_path and not Path(export_path).exists():
                continue

            # Generate snippet
            snippet = self._generate_snippet(content, query)

            # Apply recency scoring
            final_score = self._calculate_recency_score(modified, score)

            # Extract first/last message fields
            first_msg_role = doc.get_first("first_msg_role") or ""
            first_msg_content = doc.get_first("first_msg_content") or ""
            last_msg_role = doc.get_first("last_msg_role") or ""
            last_msg_content = doc.get_first("last_msg_content") or ""

            results.append({
                "session_id": session_id,
                "agent": agent,
                "project": doc_project,
                "branch": branch,
                "cwd": cwd,
                "created": created,
                "modified": modified,
                "lines": lines,
                "export_path": export_path,
                "snippet": snippet,
                "score": final_score,
                "first_msg_role": first_msg_role,
                "first_msg_content": first_msg_content,
                "last_msg_role": last_msg_role,
                "last_msg_content": last_msg_content,
            })

            if len(results) >= limit:
                break

        # Sort by score (recency-adjusted)
        results.sort(key=lambda x: x["score"], reverse=True)

        return results

    def get_recent(
        self, limit: int = 20, project: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """
        Get recent sessions sorted by modification time.

        Args:
            limit: Maximum results
            project: Filter to specific project

        Returns:
            List of result dicts sorted by recency
        """
        searcher = self.index.searcher()

        # Match all documents
        all_query = tantivy.Query.all_query()
        top_docs = searcher.search(all_query, limit * 3)  # Fetch extra for filtering

        results = []
        for _, doc_address in top_docs.hits:
            doc = searcher.doc(doc_address)

            doc_project = doc.get_first("project")
            export_path = doc.get_first("export_path")

            # Apply project filter
            if project and doc_project != project:
                continue

            # Skip if session file no longer exists (deleted outside of search)
            if export_path and not Path(export_path).exists():
                continue

            modified = doc.get_first("modified")
            content = doc.get_first("content")

            results.append({
                "session_id": doc.get_first("session_id"),
                "agent": doc.get_first("agent"),
                "project": doc_project,
                "branch": doc.get_first("branch"),
                "cwd": doc.get_first("cwd"),
                "created": doc.get_first("created"),
                "modified": modified,
                "lines": doc.get_first("lines"),
                "export_path": export_path,
                "snippet": self._generate_snippet(content, ""),
                "score": 0.0,
                "first_msg_role": doc.get_first("first_msg_role") or "",
                "first_msg_content": doc.get_first("first_msg_content") or "",
                "last_msg_role": doc.get_first("last_msg_role") or "",
                "last_msg_content": doc.get_first("last_msg_content") or "",
                "claude_home": doc.get_first("claude_home") or "",
            })

        # Sort by modified timestamp (most recent first)
        results.sort(key=lambda x: x["modified"] or "", reverse=True)

        return results[:limit]

    def get_latest_session(
        self,
        cwd: Optional[str] = None,
        branch: Optional[str] = None,
        agent: Optional[str] = None,
        include_sub_agents: bool = False,
    ) -> Optional[dict[str, Any]]:
        """
        Get the most recent session matching the given criteria.

        Uses Tantivy's native sorting by modified_ts fast field for efficiency.

        Args:
            cwd: Filter to sessions from this working directory
            branch: Filter to sessions on this git branch
            agent: Filter to specific agent ("claude" or "codex")
            include_sub_agents: If False (default), exclude sub-agent sessions

        Returns:
            Dict with session info, or None if no matching session found
        """
        searcher = self.index.searcher()

        # Build query string for filtering
        # Use parse_query with field:value syntax for exact matches
        query_terms = []

        if agent:
            query_terms.append(f'agent:"{agent}"')

        if cwd:
            # Escape special chars in cwd path
            escaped_cwd = cwd.replace('"', '\\"')
            query_terms.append(f'cwd:"{escaped_cwd}"')

        if branch:
            query_terms.append(f'branch:"{branch}"')

        # Combine with AND, or use match-all if no filters
        if query_terms:
            query_str = " AND ".join(query_terms)
            query = self.index.parse_query(query_str, ["content"])
        else:
            query = tantivy.Query.all_query()

        # Search with native sorting by modified_ts descending (most recent first)
        # Fetch enough to find valid ones after filtering deleted files and sub-agents
        top_docs = searcher.search(
            query,
            limit=100,
            order_by_field="modified_ts",
            order=tantivy.Order.Desc,
        )

        # Iterate through results (already sorted by recency) and return first valid
        for _, doc_address in top_docs.hits:
            doc = searcher.doc(doc_address)
            export_path = doc.get_first("export_path")

            # Skip if session file no longer exists
            if export_path and not Path(export_path).exists():
                continue

            # Filter out sub-agents unless requested
            if not include_sub_agents:
                if export_path and (
                    "/agent-" in export_path or
                    export_path.split("/")[-1].startswith("agent-")
                ):
                    continue

            # Found a valid session - return it
            modified = doc.get_first("modified")
            content = doc.get_first("content")

            return {
                "session_id": doc.get_first("session_id"),
                "agent": doc.get_first("agent"),
                "project": doc.get_first("project"),
                "branch": doc.get_first("branch"),
                "cwd": doc.get_first("cwd"),
                "created": doc.get_first("created"),
                "modified": modified,
                "lines": doc.get_first("lines"),
                "export_path": export_path,
                "snippet": self._generate_snippet(content, "") if content else "",
                "score": 0.0,
                "first_msg_role": doc.get_first("first_msg_role") or "",
                "first_msg_content": doc.get_first("first_msg_content") or "",
                "last_msg_role": doc.get_first("last_msg_role") or "",
                "last_msg_content": doc.get_first("last_msg_content") or "",
                "derivation_type": doc.get_first("derivation_type") or "",
                "is_sidechain": doc.get_first("is_sidechain") or "false",
            }

        return None


def get_latest_session_from_index(
    cwd: Optional[str] = None,
    branch: Optional[str] = None,
    agent: Optional[str] = None,
    index_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """
    Convenience function to get latest session from the index.

    Args:
        cwd: Filter to sessions from this working directory
        branch: Filter to sessions on this git branch
        agent: Filter to specific agent ("claude" or "codex")
        index_path: Path to index (default: ~/.cctools/search-index)

    Returns:
        Dict with session info including 'session_id' and 'export_path',
        or None if no matching session found
    """
    if index_path is None:
        index_path = Path.home() / ".cctools" / "search-index"

    if not index_path.exists():
        return None

    try:
        idx = SessionIndex(index_path)
        return idx.get_latest_session(cwd=cwd, branch=branch, agent=agent)
    except Exception:
        return None


def auto_index(
    index_path: Optional[Path] = None,
    claude_home: Optional[Path] = None,
    codex_home: Optional[Path] = None,
    verbose: bool = False,
    silent: bool = False,
) -> dict[str, Any]:
    """
    Automatically index new/changed session files on launch.

    This implements the "zero-config" Recall model approach:
    - Scans session directories for JSONL files
    - Indexes new/modified files incrementally
    - Returns quickly if nothing changed

    Args:
        index_path: Path to Tantivy index (default: ~/.cctools/search-index)
        claude_home: Claude home directory (default: ~/.claude)
        codex_home: Codex home directory (default: ~/.codex)
        verbose: If True, print progress messages
        silent: If True, suppress ALL output including progress bars (for JSON mode)

    Returns:
        Dict with stats: {indexed, skipped, failed, total_files}
    """
    _require_deps()

    # Default paths
    if index_path is None:
        index_path = Path.home() / ".cctools" / "search-index"
    if claude_home is None:
        claude_home = Path.home() / ".claude"
    if codex_home is None:
        codex_home = Path.home() / ".codex"

    # Find all JSONL session files
    jsonl_files: list[Path] = []
    claude_file_count = 0
    codex_file_count = 0

    # Claude sessions: ~/.claude/projects/**/*.jsonl
    claude_projects = claude_home / "projects"
    if claude_projects.exists():
        claude_files = list(claude_projects.glob("**/*.jsonl"))
        claude_file_count = len(claude_files)
        jsonl_files.extend(claude_files)

    # Codex sessions: ~/.codex/sessions/YYYY/MM/DD/*.jsonl (fixed depth)
    codex_sessions = codex_home / "sessions"
    if codex_sessions.exists():
        codex_files = list(codex_sessions.glob("????/??/??/*.jsonl"))
        codex_file_count = len(codex_files)
        jsonl_files.extend(codex_files)

    if verbose and not silent:
        print(f"Claude home: {claude_home} ({claude_file_count} files)")
        print(f"Codex home:  {codex_home} ({codex_file_count} files)")
        print(f"Total: {len(jsonl_files)} session files to check")

    if not jsonl_files:
        return {
            "indexed": 0, "skipped": 0, "failed": 0,
            "total_files": 0, "claude_files": 0, "codex_files": 0,
        }

    # Create/open index and run incremental indexing
    # In silent mode, suppress tqdm progress bar for clean JSON output
    index = SessionIndex(index_path)
    stats = index.index_from_jsonl(
        jsonl_files,
        incremental=True,
        claude_home=claude_home,
        codex_home=codex_home,
        show_progress=not silent,
    )
    stats["total_files"] = len(jsonl_files)
    stats["claude_files"] = claude_file_count
    stats["codex_files"] = codex_file_count

    # Prune deleted sessions from index
    pruned = index.prune_deleted()
    stats["pruned"] = pruned

    # Always show indexing/pruning activity (even non-verbose), but suppress in silent mode
    if not silent:
        if stats["indexed"] > 0:
            print(f"Indexed {stats['indexed']} new/modified sessions")
        if pruned > 0:
            print(f"Pruned {pruned} deleted sessions from index")
        # Only show "up to date" in verbose mode (avoid noise on every command)
        if verbose and stats["indexed"] == 0 and pruned == 0:
            print("Index up to date")

    return stats
