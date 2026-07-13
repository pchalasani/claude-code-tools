import { chmod, writeFile } from "node:fs/promises";
import path from "node:path";

export async function createFakeCodex(directory: string): Promise<string> {
  const scriptPath = path.join(directory, "fake-codex.mjs");
  const source = `#!/usr/bin/env node
import { spawn } from "node:child_process";
import { appendFile, writeFile } from "node:fs/promises";

let prompt = "";
for await (const chunk of process.stdin) {
  prompt += chunk.toString("utf8");
}
const record = {
  args: process.argv.slice(2),
  prompt,
  startedAt: Date.now(),
};
if (process.env.FAKE_CODEX_LOG) {
  await appendFile(
    process.env.FAKE_CODEX_LOG,
    JSON.stringify(record) + "\\n",
    "utf8",
  );
}
const delay = Number(prompt.match(/\\[delay=(\\d+)\\]/)?.[1] ?? 0);
const stubbornGrandchildPath =
  prompt.match(/\\[grandchild-ignore=([^\\]]+)\\]/)?.[1];
const grandchildPath =
  prompt.match(/\\[grandchild=([^\\]]+)\\]/)?.[1] ?? stubbornGrandchildPath;
if (grandchildPath) {
  const grandchildSource = stubbornGrandchildPath
    ? "const { writeFileSync } = require('node:fs'); " +
      "process.on('SIGTERM', () => {}); " +
      "writeFileSync(" + JSON.stringify(stubbornGrandchildPath) +
      ", String(process.pid), 'utf8'); " +
      "setInterval(() => {}, 1000)"
    : "setInterval(() => {}, 1000)";
  const grandchild = spawn(
    process.execPath,
    ["-e", grandchildSource],
    { stdio: "ignore" },
  );
  if (!stubbornGrandchildPath) {
    await writeFile(grandchildPath, String(grandchild.pid), "utf8");
  }
}
if (delay > 0) {
  await new Promise((resolve) => setTimeout(resolve, delay));
}
if (prompt.includes("[context-fail]")) {
  console.log(JSON.stringify({
    type: "thread.started",
    thread_id: "thread-context-failure",
  }));
  console.log(JSON.stringify({
    type: "turn.completed",
    usage: {
      input_tokens: 99,
      cached_input_tokens: 3,
      output_tokens: 1,
    },
  }));
  console.log(JSON.stringify({
    type: "turn.failed",
    error: { message: "context_length_exceeded" },
  }));
  process.exit(1);
}
if (prompt.includes("[fail]")) {
  process.stderr.write("synthetic worker failure\\n");
  process.exit(17);
}
const structured = process.argv.includes("--output-schema");
const text = structured
  ? JSON.stringify({ items: ["a", "b", "c"] })
  : "result:" + prompt;
console.log(JSON.stringify({
  type: "thread.started",
  thread_id: "thread-" + Math.random().toString(16).slice(2),
}));
console.log(JSON.stringify({
  type: "item.completed",
  item: { type: "agent_message", text },
}));
console.log(JSON.stringify({
  type: "turn.completed",
  usage: {
    input_tokens: 10,
    cached_input_tokens: 2,
    output_tokens: 4,
  },
}));
`;
  await writeFile(scriptPath, source, "utf8");
  await chmod(scriptPath, 0o755);
  return scriptPath;
}

export async function waitFor(
  predicate: () => boolean,
  timeoutMs = 3_000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) {
      throw new Error("Timed out waiting for condition");
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}
