# Changelog

## [1.10.7] - 2026-02-19

### Fixed

- **Reduced ffplay startup latency**: Added `-probesize 32` and
  `-analyzeduration 0` flags to the `ffplay` streaming command.
  Reduces time-to-first-audio by ~0.6s by making ffplay start
  playback sooner instead of buffering excess data. (#60)

## [1.10.4] - 2026-02-11

### Fixed

- **Safer hook file-existence check**: Replaced `&&/||` bash pattern
  with `if/then/else/fi` so only missing files fail open. Runtime
  errors (import failures, missing python3) now properly propagate
  instead of silently approving.

## [1.10.3] - 2026-02-06

### Fixed

- **Stale voice update on rapid turns**: The stop hook could speak the
  *previous* turn's message when the JSONL hadn't flushed the current
  turn yet. The existing retry mechanism only triggered when the
  ordering check failed — but stale data from the previous turn passed
  the check, so it was returned immediately without retrying.

  The fix tracks the last-spoken JSONL line number in a per-session
  state file (`/tmp/voice-last-spoken-{session_id}.json`). On each
  invocation, if the candidate line matches the previously spoken line,
  it's treated as stale and the hook retries until new data appears or
  the 5-second window expires.

### Improved

- **Faster session file lookup**: Uses `transcript_path` from the
  hook's stdin data directly instead of searching all project
  directories via `find_session_file()`. Falls back to the search
  only if `transcript_path` is missing or doesn't exist.

## [1.9.3] - 2026-01-30

### Fixed

- **Stale summary race condition**: When Claude streams responses, thinking
  entries are written to the session file before text entries. If the stop hook
  fired between these writes, it would return the previous message's text
  instead of the current one, causing wrong audio to play.

  The fix uses line-order verification: checks that the last assistant message
  with text appears AFTER the last user message in the session file. Retries
  up to 10 times (500ms delay each, 5s total) if the current response hasn't
  been written yet. This handles both the thinking-before-text race and cases
  where the entire assistant message is delayed.

## [1.9.2] - 2026-01-29

### Added
- **UserPromptSubmit hook**: Injects voice summary instructions at the start of
  each turn, reminding Claude to add a `📢` spoken summary marker
- **PostToolUse hook**: Short reminder after each tool call to keep voice
  instructions fresh during long tool chains
- **voice_common.py**: Shared module for DRY code (config reading, reminder
  building, constants)
- **Silent hook injection**: Uses `additionalContext` instead of `systemMessage`
  to avoid noisy terminal output
- **MAX_SPOKEN_WORDS constant**: Configurable word limit (default 25) in
  `voice_common.py`
- **TTS benchmark script**: `scripts/benchmark_tts.py` for comparing KittenTTS
  vs pocket-tts performance
- **just_disabled flag**: When voice is disabled via `/voice:speak stop`, a
  one-time "do NOT add 📢 summaries" message is injected to counteract earlier
  voice instructions still in context

### Changed
- **Smarter summary extraction**: Extracts inline `📢` markers for instant
  summaries (no API call needed)
- **Word-based length detection**: Uses word count (≤25 words) instead of
  fragile sentence counting
- **Fallback only when needed**: Headless Claude summarization only triggers
  for responses >25 words without a marker
- **Text block joining**: Fixed joining with newlines (not spaces) so `📢`
  markers at start of text blocks are properly detected
- **Quiet Stop hook**: Only shows output when headless Claude generates a new
  summary; otherwise runs silently
- **Flexible word limits**: Explicit summaries (📢 marker or headless Claude)
  use 1.5× the word limit, giving Claude flexibility while preventing runaway
  verbosity. Strict limit only applies to last-resort truncation.

### Improved
- **Tone matching**: Instructions now tell Claude to match user's tone and
  style (including colorful language)
- **Custom prompt support**: User's custom voice prompt (from config) is
  included and noted to override default instructions if conflicting

## [1.8.4] - 2026-01-26

### Initial release
- Stop hook with headless Claude summarization
- pocket-tts integration for voice synthesis
- Configurable voice selection
- Custom prompt support via `~/.claude/voice.local.md`
