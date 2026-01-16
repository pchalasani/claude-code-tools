---
allowed-tools: Bash, Read, Write, Edit
arguments: voice
---

Set the voice for voice-feedback plugin, or disable/enable voice feedback.

**Commands:**
- `/say-voice` - Show current voice and enabled status
- `/say-voice <voice>` - Set voice (e.g., azure, alba) and enable feedback
- `/say-voice stop` - Disable voice feedback

**Config file:** `~/.claude/voice-feedback.local.md`

```yaml
---
voice: azure
enabled: true
---
```

When setting a voice, also set `enabled: true`.
When using `stop`, set `enabled: false` (voice unchanged).
Create the config file if it doesn't exist.
