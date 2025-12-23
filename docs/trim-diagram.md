# Trim Functionality

```
                        ┌─────────────────────────────────┐
                        │       ORIGINAL SESSION          │
                        │  ┌───────────────────────────┐  │
                        │  │ user: "fix the bug"       │  │
                        │  │ assistant: ...            │  │
                        │  │ tool: ██████████ 15K ch   │  │
                        │  │ assistant: ████████ 8K    │  │
                        │  │ tool: ████████████ 25K    │  │
                        │  │ user: "now add tests"     │  │
                        │  │ ...                       │  │
                        │  └───────────────────────────┘  │
                        │       Context: 85% full ⚠️       │
                        └───────────────┬─────────────────┘
                                        │
                        ┌───────────────┴───────────────┐
                        ▼                               ▼
            ┌───────────────────┐           ┌───────────────────┐
            │   aichat trim     │           │ aichat smart-trim │
            ├───────────────────┤           ├───────────────────┤
            │ • Threshold-based │           │ • LLM-powered     │
            │ • Fast, 500ch def │           │ • Context-aware   │
            │ • Filter by tool  │           │ • Task-preserving │
            └─────────┬─────────┘           └─────────┬─────────┘
                      └───────────┬───────────────────┘
                                  ▼
                        ┌─────────────────────────────────┐
                        │       TRIMMED SESSION           │
                        │  ┌───────────────────────────┐  │
                        │  │ [LINEAGE: parent.jsonl]   │  │
                        │  │ user: "fix the bug"       │  │
                        │  │ assistant: ...            │  │
                        │  │ tool: [truncated → L:42]  │  │
                        │  │ assistant: [trunc → L:58] │  │
                        │  │ tool: [truncated → L:73]  │  │
                        │  │ user: "now add tests"     │  │
                        │  └───────────────────────────┘  │
                        │       Context: 40% full ✓        │
                        │   New UUID • Lineage preserved   │
                        └─────────────────────────────────┘
                                        │
                        ┌───────────────┴───────────────┐
                        ▼                               ▼
               ┌─────────────────┐            ┌─────────────────┐
               │ Resume session  │            │  Need details?  │
               │ claude --resume │            │  Agent reads    │
               │                 │            │  parent file    │
               └─────────────────┘            └─────────────────┘


                              LINEAGE CHAIN
    ┌──────────┐      ┌──────────┐      ┌──────────┐
    │ Original │ ───► │ Trim #1  │ ───► │ Trim #2  │ ───► ...
    │ (abc123) │      │ (def456) │      │ (ghi789) │
    └──────────┘      └──────────┘      └──────────┘
          ▲                 ▲                 ▲
          └─────────────────┴─────────────────┘
                  Agent can read any ancestor
```

**Key Points:**
- Trim creates a *new* session (original preserved)
- Truncated content references parent file line numbers
- 30-50% context savings typical on first trim
- Chain trims until rollover becomes better option
