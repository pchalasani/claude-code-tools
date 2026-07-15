"""Test that search commands handle missing dependencies gracefully."""

import subprocess
import sys

SUBPROCESS_TIMEOUT_SECONDS = 10


def test_export_session_import_error_handling():
    """
    Importing export_session module should not crash if yaml is missing.
    """
    try:
        from claude_code_tools import export_session

        if not hasattr(export_session, "YAML_AVAILABLE"):
            assert False, (
                "export_session should define YAML_AVAILABLE to handle "
                "missing pyyaml gracefully"
            )
    except ImportError as e:
        if "yaml" in str(e).lower():
            assert False, (
                f"export_session should not raise ImportError at module level: {e}"
            )
        raise


def test_search_command_without_deps_gives_helpful_error():
    """
    When tantivy or yaml is not installed, 'aichat search' should give a
    helpful error message rather than a raw ImportError traceback.
    """
    # Run aichat search in a subprocess to simulate the user's environment
    # We use --help since it should work even without deps if imports are lazy
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from claude_code_tools.aichat import main; main()",
            "search",
            "--help",
        ],
        capture_output=True,
        text=True,
        env={"PATH": ""},  # Minimal env
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )

    # Should not crash with ImportError at module load time
    # Either works (if deps installed) or gives helpful message
    assert "ModuleNotFoundError" not in result.stderr, (
        "Should handle missing dependencies gracefully, not crash on import"
    )


def test_search_index_import_error_handling():
    """
    Importing search_index module should not crash if tantivy/yaml is missing.
    Instead, the error should be deferred until actually using the index.
    """
    # This test verifies the import doesn't fail at module level
    # The actual functionality test happens when SessionIndex is instantiated
    try:
        # This should not raise ImportError at import time
        from claude_code_tools import search_index

        # Check that lazy import flags are defined
        required_flags = ["TANTIVY_AVAILABLE", "YAML_AVAILABLE"]
        missing_flags = [f for f in required_flags if not hasattr(search_index, f)]

        if missing_flags:
            assert False, (
                f"search_index should define {missing_flags} to handle "
                "missing dependencies gracefully"
            )
    except ImportError as e:
        # Should not raise ImportError for tantivy or yaml at module level
        err_str = str(e).lower()
        if "tantivy" in err_str or "yaml" in err_str:
            assert False, (
                f"search_index should not raise ImportError at module level: {e}"
            )
        raise
