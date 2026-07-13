import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

export default defineConfig({
  site: "https://pchalasani.github.io",
  base: "/claude-code-tools",
  legacy: { collections: true },
  integrations: [
    starlight({
      title: "claude-code-tools",
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/pchalasani/claude-code-tools",
        },
      ],
      sidebar: [
        {
          label: "Getting Started",
          items: [
            {
              label: "Installation & Setup",
              slug: "getting-started",
            },
            {
              label: "Plugins",
              slug: "getting-started/plugins",
            },
          ],
        },
        {
          label: "Tools",
          items: [
            {
              label: "aichat",
              collapsed: false,
              items: [
                {
                  label: "Overview",
                  slug: "tools/aichat",
                },
                {
                  label: "Resume",
                  slug: "tools/aichat/resume",
                },
                {
                  label: "Search",
                  slug: "tools/aichat/search",
                },
                {
                  label: "Session Actions",
                  slug: "tools/aichat/actions",
                },
                {
                  label: "Agent Access",
                  slug: "tools/aichat/agent-access",
                },
                {
                  label: "Rollover Details",
                  slug: "tools/aichat/rollover-details",
                },
              ],
            },
            {
              label: "tmux-cli",
              collapsed: false,
              items: [
                {
                  label: "Overview",
                  slug: "tools/tmux-cli",
                },
                {
                  label: "Command Reference",
                  slug: "tools/tmux-cli/reference",
                },
                {
                  label: "Resources",
                  slug: "tools/tmux-cli/resources",
                },
              ],
            },
            { label: "lmsh", slug: "tools/lmsh" },
            { label: "fix-session", slug: "tools/fix-session" },
            { label: "Status Line", slug: "tools/statusline" },
            { label: "vault", slug: "tools/vault" },
            { label: "env-safe", slug: "tools/env-safe" },
            { label: "sasy-guard", slug: "tools/sasy-guard" },
            { label: "agent-tunnel", slug: "tools/agent-tunnel" },
          ],
        },
        {
          label: "Plugins",
          items: [
            {
              label: "Safety Hooks",
              slug: "plugins-detail/safety-hooks",
            },
            { label: "Voice", slug: "plugins-detail/voice" },
            {
              label: "Workflow",
              slug: "plugins-detail/workflow",
            },
            {
              label: "Dynamic Workflow",
              slug: "plugins-detail/dynamic-workflow",
            },
            {
              label: "Langroid",
              slug: "plugins-detail/langroid",
            },
          ],
        },
        {
          label: "Integrations",
          items: [
            {
              label: "Alt LLM Providers",
              slug: "integrations/alt-llm-providers",
            },
            {
              label: "Local LLMs",
              slug: "integrations/local-llms",
            },
            {
              label: "Google Docs",
              slug: "integrations/google-docs",
            },
            {
              label: "Google Sheets",
              slug: "integrations/google-sheets",
            },
          ],
        },
        {
          label: "Guides",
          items: [
            {
              label: "Claude → Codex",
              slug: "guides/claude-to-codex",
            },
            {
              label: "Zsh Setup",
              slug: "guides/zsh-setup",
            },
          ],
        },
        {
          label: "Development",
          items: [
            {
              label: "Overview",
              slug: "development",
            },
            {
              label: "Contributing",
              slug: "development/contributing",
            },
            {
              label: "Make Commands",
              slug: "development/make-commands",
            },
            {
              label: "Publishing",
              slug: "development/publishing",
            },
            {
              label: "Testing",
              slug: "development/testing",
            },
          ],
        },
      ],
    }),
  ],
});
