---
title: "Testing"
description: >
  How to run tests and verify changes across the
  Python, Rust, and Node.js layers.
---

## Running Tests

Python tests use **pytest**. Run the full suite:

```bash
pytest -xvs tests/
```

Run a specific test file:

```bash
pytest -xvs tests/test_trim_session.py
```

## Test Coverage

The test suite covers:

| Area | Test Files |
|------|-----------|
| Session search & indexing | `test_search_index.py`, `test_search_import.py`, `test_aichat_search_truncate.py` |
| Session finding & filtering | `test_find_sessions.py`, `test_find_session_filtering.py` |
| Session ID resolution | `test_session_id_resolution.py`, `test_session_id_consistency.py`, `test_session_resolution.py` |
| Trim & smart trim | `test_trim_session.py`, `test_smart_trim.py`, `test_smart_trim_mock.py` |
| Resume / continue flow | `test_continue_flow.py` |
| Export | `test_export_yaml.py` |
| Sidechain sessions | `test_sidechain_sessions.py` |
| Codex integration | `test_codex_clone_codex_home.py` |
| tmux-cli | `test_tmux_cli_controller.py`, `test_tmux_execution_helpers.py` |
| Utilities | `test_command_utils.py` |

## Rebuild After Changes

- **Python** -- Editable mode (`make install`), so
  changes apply immediately. No reinstall needed.
- **Node.js** -- Runs directly from `node_ui/`. No
  build step. Run `make node-ui-deps` if you add new
  npm dependencies, or in a fresh clone or worktree
  that has no `node_ui/node_modules` yet.
- **Rust** -- Must rebuild after changes:

  ```bash
  make aichat-search-install  # aichat-search binary
  make lmsh-install            # lmsh binary
  ```

## See Also

- [Development overview](../) -- architecture and
  setup instructions
- [Make Commands](../make-commands/) -- full list of
  build and install targets
