"""agent-tunnel: expose a local Claude Code session to teammates.

A small daemon that answers questions over a chat front-end (Discord or
Slack) against forks of a long-lived "expert" Claude Code session, with
hard read-only tool restrictions. The two front-ends are thin adapters
over a shared, platform-neutral core. See docs/agent-tunnel-spec.md and
docs/agent-tunnel-slack-spec.md for the design.
"""
