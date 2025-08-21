#!/usr/bin/env python3
import re
import os
import subprocess
from pathlib import Path

def check_git_add_command(command):
    """
    Check if a git add command contains dangerous patterns.
    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Normalize the command - handle multiple spaces, tabs, etc.
    normalized_cmd = ' '.join(command.strip().split())
    
    # Pattern to match git add with problematic flags and dangerous patterns
    # Check for wildcards or dangerous patterns anywhere in the arguments
    if '*' in normalized_cmd and normalized_cmd.startswith('git add'):
        reason = """BLOCKED: Wildcard patterns are not allowed in git add!
        
DO NOT use wildcards like 'git add *.py' or 'git add *'

Instead, use:
- 'git add <specific-files>' to stage specific files
- 'git ls-files -m "*.py" | xargs git add' if you really need pattern matching

This restriction prevents accidentally staging unwanted files."""
        return True, reason
    
    # Hard block patterns: -A, --all, -a, ., ../, etc.
    dangerous_pattern = re.compile(
        r'^git\s+add\s+(?:.*\s+)?('
        r'-[a-zA-Z]*[Aa][a-zA-Z]*(\s|$)|'  # Flags containing 'A' or 'a'
        r'--all(\s|$)|'                     # Long form --all
        r'\.(\s|$)|'                        # git add . (current directory)
        r'\.\./[\.\w/]*(\s|$)'             # git add ../ or ../.. patterns
        r')', re.IGNORECASE
    )
    
    if dangerous_pattern.search(normalized_cmd):
        reason = """BLOCKED: Dangerous git add pattern detected!
        
DO NOT use:
- 'git add -A', 'git add -a', 'git add --all' (adds ALL files)
- 'git add .' (adds entire current directory)
- 'git add ../' or similar parent directory patterns
- 'git add *' (wildcard patterns)

Instead, use:
- 'git add <specific-files>' to stage specific files
- 'git add <specific-directory>/' to stage a specific directory (with confirmation)
- 'git add -u' to stage all modified/deleted files (but not untracked)

This restriction prevents accidentally staging unwanted files."""
        return True, reason
    
    # Check for git add with a directory (speed bump pattern)
    # Match: git add <dirname>/ or git add <path/to/dir>/
    directory_pattern = re.compile(r'^git\s+add\s+(?!-)[^\s]+/$')
    match = directory_pattern.search(normalized_cmd)
    
    if match:
        # Extract the directory path from the command
        parts = normalized_cmd.split()
        dir_path = None
        for i, part in enumerate(parts):
            if i > 0 and parts[i-1] == 'add' and part.endswith('/'):
                dir_path = part.rstrip('/')
                break
        
        if dir_path:
            # Check if flag file exists (second attempt)
            flag_file = Path(f'.claude_git_add_dir_{dir_path.replace("/", "_")}.flag')
            
            if flag_file.exists():
                # Second attempt - delete flag and allow
                flag_file.unlink()
                return False, None
            
            # First attempt - create flag and show warning with file list
            flag_file.touch()
            
            # Try to list files that would be staged
            try:
                # Get list of files that would be added
                result = subprocess.run(
                    ['git', 'ls-files', '--others', '--modified', '--cached', dir_path],
                    capture_output=True, text=True, cwd=os.getcwd()
                )
                files = [f for f in result.stdout.strip().split('\n') if f]
                
                # Also get untracked files in the directory
                untracked_result = subprocess.run(
                    ['git', 'ls-files', '--others', '--exclude-standard', dir_path],
                    capture_output=True, text=True, cwd=os.getcwd()
                )
                untracked = [f for f in untracked_result.stdout.strip().split('\n') if f]
                
                # Combine and deduplicate
                all_files = sorted(set(files + untracked))
                
                file_list = ""
                if all_files:
                    if len(all_files) <= 10:
                        file_list = "\n".join(f"  - {f}" for f in all_files)
                    else:
                        file_list = "\n".join(f"  - {f}" for f in all_files[:10])
                        file_list += f"\n  ... and {len(all_files) - 10} more files"
                else:
                    file_list = "  (no files found - directory may be empty or already staged)"
                
                reason = f"""⚠️  Git add directory blocked (first attempt).

You're trying to stage all files in directory: {dir_path}/

Files that would be staged:
{file_list}

If you really want to stage all these files, retry the command.
Otherwise, use 'git add <specific-files>' to stage only the files you need."""
                
            except Exception:
                # If we can't list files, still show warning
                reason = f"""⚠️  Git add directory blocked (first attempt).

You're trying to stage all files in directory: {dir_path}/

If you really want to stage all files in this directory, retry the command.
Otherwise, use 'git add <specific-files>' to stage only the files you need."""
            
            return True, reason
    
    # Also check for git commit -a without -m (which would open an editor)
    # Check if command has -a flag but no -m flag
    if re.search(r'^git\s+commit\s+', normalized_cmd):
        has_a_flag = re.search(r'-[a-zA-Z]*a[a-zA-Z]*', normalized_cmd)
        has_m_flag = re.search(r'-[a-zA-Z]*m[a-zA-Z]*', normalized_cmd)
        if has_a_flag and not has_m_flag:
            reason = """Avoid 'git commit -a' without a message flag. Use 'gcam "message"' instead, which is an alias for 'git commit -a -m'."""
            return True, reason
    
    return False, None


# If run as a standalone script
if __name__ == "__main__":
    import json
    import sys
    
    data = json.load(sys.stdin)
    
    # Check if this is a Bash tool call
    tool_name = data.get("tool_name")
    if tool_name != "Bash":
        print(json.dumps({"decision": "approve"}))
        sys.exit(0)
    
    # Get the command being executed
    command = data.get("tool_input", {}).get("command", "")
    
    should_block, reason = check_git_add_command(command)
    
    if should_block:
        print(json.dumps({
            "decision": "block",
            "reason": reason
        }))
    else:
        print(json.dumps({"decision": "approve"}))
    
    sys.exit(0)