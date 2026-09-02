# Visual Brief Milestone Reminder Specification

Status: implementation contract. Builds on `317b26b`.

## Purpose

Agents sometimes publish a Visual Brief and then forget to publish again after
a long block of useful work. The product should quietly refresh that intention
inside the agent's context without interrupting the human or forcing a publish.

The reminder is session-scoped and provider-neutral. Claude Code and Codex use
thin lifecycle adapters over one policy and one durable state format.

## Activation

A session starts inactive. Creating, viewing, serving, watching, folding, or
answering a Visual Brief does not activate reminders.

`visual-brief publish` activates reminders only after it has successfully saved
and rendered the briefing and its successful Bash or shell `PostToolUse` event
has reached the adapter. The adapter associates that publish with the current
agent session using the hook payload's required `session_id`. Publishing itself
has no reminder imports, state writes, or provider-environment coupling.

A failed publish, incidental or quoted publish text, a non-publish Visual Brief
verb, a missing session identity, or a malformed tool result does not activate
reminders. Reminder-state failures never alter the completed tool result: the
hook still exits zero with a valid empty response.

Each successful publish resets the session's meaningful-work counter and
records a new publish timestamp.

## Reminder Policy

Provider hooks invoke the policy after meaningful tool completions. Pure reads,
searches, navigation, and status checks do not count as meaningful work.
Meaningful work includes source edits and successful build, test, formatting,
review, or commit commands.

The default policy emits a reminder only after both conditions hold:

- at least 20 minutes have passed since the latest publish or reminder;
- at least three meaningful tool completions have occurred since that point.

Tests may override both thresholds through explicit environment variables. A
reminder resets both gates. Continued work can therefore cause another reminder
after another complete interval, but ordinary tool chains cannot spam context.

The exact reminder is:

> Visual Brief is active for this session. Remember to publish an update when
> you reach the next meaningful milestone.

The wording is advisory. Hooks must not block a tool, reject a turn, continue a
stopping turn, tell the agent to publish immediately, or display a warning to
the human.

## Shared Engine and State

The shared Python engine owns activation, gating, locking, and state updates.
Provider adapters only validate lifecycle input, classify a completed tool, and
format quiet context output.

State lives below the configured Visual Brief home in the hidden dedicated
`.reminders` directory. One opaque, hashed filename represents each provider
and session. Each file records its schema version, provider, activation time,
last gate time, and meaningful-work count.

Every read-modify-write operation holds a per-session file lock. State files
use atomic replacement. A malformed or unsupported state file fails closed for
that invocation: the hook emits no reminder and does not invent activation.

Old session records are small and harmless. Automatic retention cleanup is out
of scope for this change.

## Provider Adapters

The Visual Brief package is also a hook-only plugin source for both providers.
The repository marketplaces expose that plugin without copying the policy into
provider packages.

One shared hook invokes the adapter in `auto` mode. The adapter selects Codex
when its guaranteed `PLUGIN_ROOT` is nonempty, otherwise Claude when
`CLAUDE_PLUGIN_ROOT` is nonempty, and otherwise fails closed. Codex also exposes
`CLAUDE_PLUGIN_ROOT` for compatibility, so `PLUGIN_ROOT` takes precedence.
Provider detection never uses `CLAUDE_SESSION_ID` or `CODEX_THREAD_ID`.
Explicit `claude` and `codex` modes remain available for direct tests.

All modes accept the provider's PostToolUse JSON on standard input, require a
non-empty payload `session_id`, and preserve canonical `tool_response` with
`tool_result` as fallback. A successful Bash or shell command containing an
actually executed `visual-brief publish` segment activates or resets the
session only when provider output contains the concrete successful CLI receipt
`publish: appended `. This requirement applies to structured Claude `stdout`
and Codex string output, including when the segment is in a pipeline. Aggregate
shell success alone is insufficient. That completion emits `{}` and does not
count as meaningful work. Other valid completions pass one meaningful-or-
trivial event to the shared engine.

Success may be reported by an explicit boolean or zero exit code. Claude's
completed Bash response is also successful when `stdout` is a string,
`stderr` is empty, `interrupted` is false, and its `isImage` and
`noOutputExpected` flags are booleans. Malformed, interrupted, error-marked,
or stderr-bearing responses never activate reminders.

Real Codex plugin-hook captures set both `PLUGIN_ROOT` and
`CLAUDE_PLUGIN_ROOT`, include `session_id` in the payload, and supply
`tool_response` as a string rather than an object. A successful
`pytest --version` supplies exactly `pytest 9.0.1\n`. A successful
`visual-brief publish -` supplies a string beginning `publish: appended ` and
including the rendered path. A failed `false` supplies an empty string and no
exit status.

The Codex adapter converts a nonempty, non-error string into an explicit
successful normalized result so meaningful output such as the pytest result
can advance the shared reminder gate. Empty strings and strings containing
common error or failure markers anywhere in their output fail closed. The
known successful `pytest --version` output remains successful. Publish
activation from a Codex string additionally requires the concrete
`publish: appended ` receipt. Command text quoted in output, unrelated
nonempty output, and other incidental mentions never activate a session.
Claude string responses remain invalid; its strict object behavior is
unchanged.

When the engine returns a reminder, each adapter emits the provider's quiet
PostToolUse `additionalContext` response. Otherwise it emits a valid empty
response. Malformed input and missing identity fail closed without changing
state.

The adapters run only for tool classes that can plausibly mark progress. The
shared classifier still rejects read-only shell commands within the broad Bash
matcher.

Codex users must review and trust the installed hook definition before it runs,
as required by Codex hook security. Claude Code follows its normal plugin hook
loading rules.

## Packaging

The plugin metadata and shared `hooks/hooks.json` live in
`plugins/visual-brief`. Both providers discover that hook, which invokes the
installed `visual-brief` CLI with `--provider auto`, so both execute the exact
engine shipped by the product. Provider manifests contain only their valid
metadata schemas; the Codex manifest does not duplicate hooks inline. The
Claude Code and Codex marketplace entries point to this same plugin root.

The PyPI package continues to ship the CLI and skill. The plugin is inert when
the CLI is unavailable and documents that prerequisite. Installing the plugin
adds lifecycle reminders; it does not create a second daemon, run store, or
policy implementation.

## TDD and Verification

Focused Python tests must first fail and then cover:

- inactivity before a successful publish;
- activation and reset only after a successful publish PostToolUse event;
- missing plugin roots and payload session identities;
- provider and session isolation;
- time and activity gates, including repeat suppression;
- non-meaningful tools and read-only shell commands;
- malformed state and concurrent updates;
- Claude Code and Codex input and provider-root normalization;
- quiet, non-blocking provider outputs;
- marketplace, plugin manifest, and wheel inclusion.

Run the full Visual Brief Python suite and existing package checks. Perform a
real provider smoke test for Claude Code and Codex with temporary homes and
shortened, controlled thresholds. Each smoke test must prove that the hook is
inert before a publish, injects the exact reminder after a successful publish
and meaningful tool completion, and remains quiet on the next tool completion.

The final committed tree receives a context-free Codex review. Open a stacked
pull request against `feat/brief-v2`, then run the GitHub Codex review loop on
the exact current head. Do not merge the pull request.
