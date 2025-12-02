#!/bin/bash
# Shell function wrapper for find-codex-session that preserves directory changes
#
# To use this, add the following line to your ~/.bashrc or ~/.zshrc:
#   source /path/to/claude-code-tools/scripts/fcs-codex-function.sh
#
# Then use 'fcs-codex' instead of 'find-codex-session' to have directory
# changes persist

fcs-codex() {
    # Check if user is asking for help
    if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
        find-codex-session --help
        return
    fi

    # Run find-codex-session in shell mode
    local output
    output=$(find-codex-session --shell "$@" | sed '/^$/d')

    # Security: Validate output before eval - only allow safe commands
    # Expected outputs: cd "path", export VAR=value, codex -r session_id
    if [[ -z "$output" ]]; then
        return 0
    elif [[ "$output" =~ ^cd\ \" ]] || \
         [[ "$output" =~ ^export\ [A-Z_]+= ]] || \
         [[ "$output" =~ ^codex\ -r\  ]]; then
        eval "$output"
    else
        echo "fcs-codex: Unexpected output format, not executing for safety" >&2
        echo "Output was: $output" >&2
        return 1
    fi
}
