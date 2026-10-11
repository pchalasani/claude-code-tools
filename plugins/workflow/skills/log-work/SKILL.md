---
name: log-work
description: >-
  Log the work segment that you did after the last work log until now in a log
  file at WORKLOG/YYYYMMDD.md where YYYYMMDD is based on today's date. The
  intent of this worklog is not to be exhaustive and super detailed. It is
  meant more as a very concise description plus, importantly, REFERENCES to
  more detailed documents such as code files or markdown documents, etc. This
  allows humans as well as AI agents to only refer to these additional
  documents if they want details. This is the progressive disclosure
  principle.
---


# log-work

1. create the file WORKLOG/YYYYMMDD.md if it doesn't already exist.
2. if the file exists, append a new TOP-LEVEL markdown section to it.
3. When you add a section, follow this format for the section header, so it has
   HH:MM timestamp followed by suitable concise topic

```markdown
# 13:45 Added feature xyz
```

In the log section include a CONCISE set of items such as the following
(Not all may apply to all situations, use your judgement):

- the session_id of the current session
- any text/md file(s) you CREATED and their purpose
- any text/md file(s) you READ as part of the current task
- short description of what you just did, including any results you got 
- which code files were created/changed



