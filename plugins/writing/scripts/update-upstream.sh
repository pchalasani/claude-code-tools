#!/usr/bin/env bash
# Refresh the vendored upstream snapshots for the writing plugin's skills.
# Run from anywhere; commits nothing. Review the diff before committing:
# freshly fetched rule text is third-party input.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> avoid-ai-writing (remove-ai-patterns)"
git clone --quiet --depth 1 \
  https://github.com/conorbronsdon/avoid-ai-writing "$TMP/aaw"
DEST="$ROOT/skills/remove-ai-patterns/upstream"
mkdir -p "$DEST/detector"
cp "$TMP/aaw/SKILL.md" "$TMP/aaw/LICENSE" "$TMP/aaw/CHANGELOG.md" "$DEST/"
# validate.js is required by the upstream SKILL.md's edit-mode verification
# step; it require()s ./patterns.js from the same directory.
cp "$TMP/aaw/detector/patterns.js" "$TMP/aaw/detector/CATEGORIES.md" \
  "$TMP/aaw/detector/validate.js" \
  "$DEST/detector/"
git -C "$TMP/aaw" log -1 \
  --format='https://github.com/conorbronsdon/avoid-ai-writing %H %s' \
  > "$DEST/UPSTREAM-PIN"

echo "==> agent-style"
git clone --quiet --depth 1 https://github.com/yzhao062/agent-style "$TMP/as"
DEST="$ROOT/skills/agent-style/references"
mkdir -p "$DEST/LICENSES"
cp "$TMP/as/RULES.md" "$TMP/as/NOTICE.md" "$DEST/"
cp "$TMP/as/LICENSES/CC-BY-4.0.txt" "$DEST/LICENSES/"
git -C "$TMP/as" log -1 \
  --format='https://github.com/yzhao062/agent-style %H %s' \
  > "$DEST/UPSTREAM-PIN"

echo "Snapshots refreshed. Review the diff before committing."
