#!/usr/bin/env node
// JSON CLI over upstream/detector/patterns.js (vendored avoid-ai-writing).
const fs = require("fs");
const path = require("path");

const AIDetector = require(
  path.join(__dirname, "..", "upstream", "detector", "patterns.js"),
);

const file = process.argv[2];
if (!file) {
  console.error("usage: detect.js <file>");
  process.exit(2);
}

const result = AIDetector.analyzeText(fs.readFileSync(file, "utf8"));
console.log(JSON.stringify(result, null, 2));
