#!/usr/bin/env python3
import os
import json
import sys


data = json.load(sys.stdin)

# Check if we're in a subtask
flag_file = '.claude_in_subtask.flag'
is_main_agent = not os.path.exists(flag_file)
# if os.path.exists(flag_file):
#     print(json.dumps({"decision": "approve"}))
#     sys.exit(0)

# Check file size
file_path = data.get("tool_input", {}).get("file_path")
offset = data.get("tool_input", {}).get("offset",0)
limit = data.get("tool_input", {}).get("limit",0) # 0 if absent

if file_path and os.path.exists(file_path):
    # Check if this is a binary file by examining its content
    def is_binary_file(filepath):
        """Check if a file is binary by looking for null bytes in first chunk"""
        try:
            with open(filepath, 'rb') as f:
                # Read first 8192 bytes (or less if file is smaller)
                chunk = f.read(8192)
                if not chunk:  # Empty file
                    return False
                
                # Files with null bytes are likely binary
                if b'\x00' in chunk:
                    return True
                
                # Try to decode as UTF-8
                try:
                    chunk.decode('utf-8')
                    return False
                except UnicodeDecodeError:
                    return True
        except Exception:
            # If we can't read the file, assume it's binary to be safe
            return True
    
    # Skip line count check for binary files
    if is_binary_file(file_path):
        print(json.dumps({"decision": "approve"}))
        sys.exit(0)

    # Count lines using pure Python (more portable, no subprocess overhead)
    def count_lines(filepath):
        with open(filepath, 'rb') as f:
            return sum(1 for _ in f)

    line_count = count_lines(file_path)
    
    # Compute effective number of lines to be read
    if limit > 0:
        # If limit is specified, we read from offset to offset+limit
        effective_lines = min(limit, max(0, line_count - offset))
    else:
        # If no limit, we read from offset to end of file
        effective_lines = max(0, line_count - offset)

    if is_main_agent and line_count > 500:
        print(json.dumps({
            "decision": "block",
            "reason": f"""
                I see you are trying to read a file with {line_count} lines,
                or a part of it. 
                Please delegate the analysis to a SUB-AGENT using your Task tool,
                so you don't bloat your context with the file content!
                """,
        }))
        sys.exit(0)
    elif (not is_main_agent) and line_count > 10_000:
        # use gemini-cli to delegate the analysis
        print(json.dumps({
            "decision": "block",
            "reason": f"""
            File too large ({line_count} lines), please use the Gemini CLI
            bash command to delegate the analysis to Gemini since it has
            a 1M-token context window! This will help you avoid bloating 
            your context.
            
            You can use Gemini CLI as in these EXAMPLES:
            
            `gemini -p "@src/somefile.py tell me at which line the definition of 
                      the function 'my_function' is located"

            `gemini -p "@package.json @src/index.js Analyze the dependencies used in the code"

            See further guidelines in claude-mds/use-gemini-cli.md
            """,
        }))
        sys.exit(0)

print(json.dumps({"decision": "approve"}))
sys.exit(0)