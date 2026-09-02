"""Typed writes: every change a run undergoes, done by the tool.

An agent that hand-writes ``content.json`` re-invents the same four mistakes:
paraphrased queue text, ``{question, answer}`` pairs, invented timestamps and
enumerations crammed into prose. These verbs make the first three impossible
and the fourth visible, and each one validates, writes atomically and
re-renders the page.
"""

from visual_brief.writes.answer import answer_command
from visual_brief.writes.creation import new_command
from visual_brief.writes.fold import fold_command
from visual_brief.writes.inputs import read_json_payload, read_text_payload
from visual_brief.writes.lint import (
    lint_command,
    lint_document,
    lint_run,
    report_lint,
)
from visual_brief.writes.panels import add_update_command
from visual_brief.writes.publish import publish_command
from visual_brief.writes.runfiles import (
    CliError,
    publish_render,
    read_content,
    resolve_run,
    save_document,
)

__all__ = [
    "CliError",
    "add_update_command",
    "answer_command",
    "fold_command",
    "lint_command",
    "lint_document",
    "lint_run",
    "new_command",
    "publish_render",
    "publish_command",
    "read_content",
    "read_json_payload",
    "read_text_payload",
    "report_lint",
    "resolve_run",
    "save_document",
]
