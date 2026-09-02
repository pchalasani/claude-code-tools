import solid from "vite-plugin-solid";
import { defineConfig } from "vitest/config";

import { inlineSafety } from "./inline-safety";

/**
 * Build exactly two front-end artifacts that the Python renderer inlines
 * verbatim. Preserve the separately maintained Codex bridge in the shared
 * static directory.
 *
 * Library mode with a single IIFE output guarantees one JS file and one CSS
 * file: no code splitting, no hashed names, no module preload, no dynamic
 * import of anything. The generated page must make zero external requests, so
 * assets are inlined as data URIs and the inline-safety plugin fails the build
 * on anything that would still reach off the machine.
 */
export default defineConfig({
  plugins: [solid(), inlineSafety()],
  build: {
    outDir: "../src/visual_brief/static",
    emptyOutDir: false,
    cssCodeSplit: false,
    sourcemap: false,
    manifest: false,
    reportCompressedSize: false,
    modulePreload: false,
    assetsInlineLimit: Number.MAX_SAFE_INTEGER,
    target: "es2022",
    minify: "oxc",
    lib: {
      entry: "src/main.tsx",
      name: "VisualBrief",
      formats: ["iife"],
      fileName: () => "visual-brief.js",
      cssFileName: "visual-brief",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["test/setup.ts"],
    include: ["src/**/*.test.ts", "src/**/*.test.tsx", "*.test.ts"],
  },
});
