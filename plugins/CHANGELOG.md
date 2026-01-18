# Plugin Changelog

## 2026-01-18

### voice

- feat: add streaming audio playback for lower latency
- fix: add 30-second cooldown to stop hook to prevent API error loops caused by
  thinking block retry issues
- fix: prevent infinite loop in stop hook by tracking block attempts
