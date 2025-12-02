#!/bin/bash
# Shell function wrapper for find-claude-session that preserves directory changes
# 
# To use this, add the following line to your ~/.bashrc or ~/.zshrc:
#   source /path/to/claude-code-tools/scripts/fcs-function.sh
#
# Then use 'fcs' instead of 'find-claude-session' to have directory changes persist

fcs() {
    # Check if user is asking for help
    if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
        find-claude-session --help
        return
    fi

    # Run find-claude-session in shell mode
    local output
    output=$(find-claude-session --shell "$@" | sed '/^$/d')

    # Security: Validate output before eval - only allow safe commands
    # Expected outputs: cd "path", export VAR=value, claude -r session_id
    if [[ -z "$output" ]]; then
        return 0
    elif [[ "$output" =~ ^cd\ \" ]] || \
         [[ "$output" =~ ^export\ [A-Z_]+= ]] || \
         [[ "$output" =~ ^claude\ -r\  ]]; then
        eval "$output"
    else
        echo "fcs: Unexpected output format, not executing for safety" >&2
        echo "Output was: $output" >&2
        return 1
    fi
}