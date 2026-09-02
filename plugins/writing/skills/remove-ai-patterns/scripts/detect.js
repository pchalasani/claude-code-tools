#!/usr/bin/env node
// JSON CLI over upstream/detector/patterns.js (vendored avoid-ai-writing).
// Usage: detect.js <file> [contextMode]
//   contextMode: general (default) | technical | marketing | personal
const fs = require("fs");
const path = require("path");

const AIDetector = require(
  path.join(__dirname, "..", "upstream", "detector", "patterns.js"),
);

const VALID_CONTEXT_MODES = ["general", "technical", "marketing", "personal"];

const file = process.argv[2];
const contextMode = process.argv[3];
if (!file) {
  console.error("usage: detect.js <file> [contextMode]");
  console.error(`  contextMode: ${VALID_CONTEXT_MODES.join(" | ")}`);
  process.exit(2);
}
if (contextMode && !VALID_CONTEXT_MODES.includes(contextMode)) {
  console.error(
    `invalid contextMode '${contextMode}'; ` +
      `valid: ${VALID_CONTEXT_MODES.join(", ")}`,
  );
  process.exit(2);
}

const options = contextMode ? { contextMode } : {};
const result = AIDetector.analyzeText(fs.readFileSync(file, "utf8"), options);
console.log(JSON.stringify(result, null, 2));
