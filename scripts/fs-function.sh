#!/bin/bash
# Shell wrapper for find-session to enable persistent directory changes
#
# Usage: Add this to your .bashrc or .zshrc:
#   source /path/to/claude-code-tools/scripts/fs-function.sh
#
# Then use:
#   fs "keywords"
#   fs -g
#   fs "bug,fix" --agents claude

fs() {
    # Check if user is asking for help
    if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
        find-session --help
        return
    fi

    # Run find-session in shell mode
    local output
    output=$(find-session --shell "$@" | sed '/^$/d')

    # Security: Validate output before eval - only allow safe commands
    # Expected outputs: cd "path", export VAR=value, claude -r session_id
    if [[ -z "$output" ]]; then
        return 0
    elif [[ "$output" =~ ^cd\ \" ]] || \
         [[ "$output" =~ ^export\ [A-Z_]+= ]] || \
         [[ "$output" =~ ^(claude|codex)\ -r\  ]]; then
        eval "$output"
    else
        echo "fs: Unexpected output format, not executing for safety" >&2
        echo "Output was: $output" >&2
        return 1
    fi
}
