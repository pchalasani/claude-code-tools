---
allowed-tools: Bash, Read, Write, Edit
arguments: voice
---

Enable, disable, or configure voice feedback.

**Commands:**
- `/speak` - Enable voice feedback with current voice
- `/speak <voice>` - Set voice (e.g., azure, alba) and enable feedback
- `/speak stop` - Disable voice feedback

**Config file:** `~/.claude/speak.local.md`

```yaml
---
voice: azure
enabled: true
---
```

**Behavior:**
- When no argument: Set `enabled: true` and tell user:
  "Voice feedback enabled. Use `/speak stop` to disable, or `/speak <name>` to change voice."
- When voice name given: Set `voice: <name>` and `enabled: true`, tell user:
  "Voice set to <name> and enabled. Use `/speak stop` to disable."
- When `stop`: Set `enabled: false` (voice unchanged), tell user:
  "Voice feedback disabled. Use `/speak` to re-enable."

Create the config file if it doesn't exist (default voice: azure).
