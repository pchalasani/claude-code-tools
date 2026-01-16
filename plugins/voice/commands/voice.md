---
allowed-tools: Bash, Read, Write, Edit
arguments: voice
---

Enable, disable, or configure voice feedback.

**Commands:**
- `/voice` - Enable voice feedback with current voice
- `/voice <voice>` - Set voice (e.g., azure, alba) and enable feedback
- `/voice stop` - Disable voice feedback

**Config file:** `~/.claude/voice.local.md`

```yaml
---
voice: azure
enabled: true
---
```

**Behavior:**
- When no argument: Set `enabled: true` and tell user:
  "Voice feedback enabled. Use `/voice stop` to disable, or `/voice <name>` to change voice."
- When voice name given: Set `voice: <name>` and `enabled: true`, tell user:
  "Voice set to <name> and enabled. Use `/voice stop` to disable."
- When `stop`: Set `enabled: false` (voice unchanged), tell user:
  "Voice feedback disabled. Use `/voice` to re-enable."

Create the config file if it doesn't exist (default voice: azure).
