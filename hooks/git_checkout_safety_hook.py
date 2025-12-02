#!/usr/bin/env python3
import subprocess
import os
import re

def check_git_checkout_command(command):
    """
    Check if a git checkout command is safe to execute.
    Returns tuple: (should_block: bool, reason: str or None)
    """
    # Check if it's a git checkout command
    if not command.strip().startswith("git checkout"):
        return False, None
    
    # Safe patterns that we should allow without checking
    if "-b" in command or "--help" in command or "-h" in command:
        return False, None
    
    # ALWAYS block these dangerous patterns
    dangerous_patterns = [
        (r'\bgit\s+checkout\s+(-f|--force)\b', 
         "'git checkout -f' FORCES checkout and DISCARDS all uncommitted changes!"),
        (r'\bgit\s+checkout\s+\.', 
         "'git checkout .' will DISCARD ALL changes in current directory!"),
        (r'\bgit\s+checkout\s+.*\s+--\s+\.', 
         "This will DISCARD ALL changes in current directory!"),
        (r'\bgit\s+checkout\s+.*\s+--\s+', 
         "This will overwrite your local file with version from another branch/commit!"),
    ]
    
    for pattern, message in dangerous_patterns:
        if re.search(pattern, command):
            reason = f"⚠️  DANGEROUS COMMAND DETECTED!\n\n{message}\n\nThis command will destroy uncommitted work without warning.\n\nSafer alternatives:\n- Use 'git stash' to save changes temporarily\n- Use 'git diff' to see what would be lost\n- Use 'git restore' for clearer syntax"
            return True, reason
    
    try:
        # First, check if there are any uncommitted changes
        status_result = subprocess.run(
            ["git", "status", "--porcelain"], 
            capture_output=True, 
            text=True,
            cwd=os.getcwd()
        )
        
        has_changes = bool(status_result.stdout.strip())
        
        # Get more detailed status if there are changes
        if has_changes:
            # Get the list of modified files
            subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True,
                text=True,
                cwd=os.getcwd()
            )
            
            subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True,
                text=True,
                cwd=os.getcwd()
            )
            
            # Count changes
            all_changes = status_result.stdout.strip().split('\n')
            modified_files = [f for f in all_changes if f.strip()]
            num_changes = len(modified_files)
            
            # Build warning message
            warning = f"WARNING: You have {num_changes} uncommitted change(s) that may be lost!\n\n"
            
            if modified_files:
                warning += "Modified files:\n"
                for change in modified_files[:10]:  # Show first 10
                    warning += f"  {change}\n"
                if num_changes > 10:
                    warning += f"  ... and {num_changes - 10} more\n"
            
            warning += "\nOptions:\n"
            warning += "1. Stash changes: git stash\n"
            warning += "2. Commit changes: git commit -am 'your message'\n"
            warning += "3. Discard changes: git restore <files>\n"
            warning += "4. Use 'git switch' instead for safer branch switching\n"
            
            # Special warning for checkout .
            if "checkout ." in command or "checkout -- ." in command:
                warning += "\n⚠️  DANGER: 'git checkout .' will DISCARD ALL local changes!"
            
            return True, warning
        
    except Exception as e:
        # If we can't determine status, err on the side of caution
        reason = f"Could not verify repository status: {str(e)}\nPlease manually check 'git status' before proceeding."
        return True, reason
    
    # No uncommitted changes, safe to proceed
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
    
    should_block, reason = check_git_checkout_command(command)
    
    if should_block:
        print(json.dumps({
            "decision": "block",
            "reason": reason
        }))
    else:
        print(json.dumps({"decision": "approve"}))
    
    sys.exit(0)