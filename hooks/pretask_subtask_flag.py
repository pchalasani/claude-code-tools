#!/usr/bin/env python3
import json
import sys

# Create a flag file indicating we're entering a subtask
flag_file = '.claude_in_subtask.flag'
with open(flag_file, 'w') as f:
    f.write('1')

print(json.dumps({"decision": "approve"}))
sys.exit(0)