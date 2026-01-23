---
allowed-tools: Bash, Read, Write, Edit
arguments: voice
---

Enable, disable, or configure voice feedback.

**Commands:**
- `/speak` - Enable voice feedback with current voice
- `/speak <voice>` - Set voice (e.g., azure, alba) and enable feedback
- `/speak stop` - Disable voice feedback
- `/speak prompt <text>` - Set custom instruction for voice summaries
- `/speak prompt` - Clear custom prompt

**Config file:** `~/.claude/voice.local.md`

```yaml
---
voice: azelma
enabled: true
prompt: "always end with 'peace out'"
---
```

**Behavior:**
- When no argument: Set `enabled: true` and tell user:
  "Voice feedback enabled. Use `/speak stop` to disable, or `/speak <name>` to change voice."
- When voice name given: Set `voice: <name>` and `enabled: true`, tell user:
  "Voice set to <name> and enabled. Use `/speak stop` to disable."
- When `stop`: Set `enabled: false` (voice unchanged), tell user:
  "Voice feedback disabled. Use `/speak` to re-enable."
- When `prompt <text>`: Set `prompt: <text>`, tell user:
  "Custom prompt set: <text>"
- When `prompt` (no text): Clear the prompt field, tell user:
  "Custom prompt cleared."

Create the config file if it doesn't exist (default voice: azelma).
