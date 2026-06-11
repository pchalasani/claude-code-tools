#!/bin/bash
# Claude Code statusline with powerline style and git status
# Uses nerd fonts and ANSI colors for rainbow effect

input=$(cat)

# Validate JSON and extract fields safely
if ! echo "$input" | jq -e . >/dev/null 2>&1; then
    echo "⚠ invalid input"
    exit 0
fi

cwd=$(echo "$input" | jq -r '.workspace.current_dir // empty' 2>/dev/null)
dir_name=$(basename "$cwd" 2>/dev/null || echo "?")

# Extract model - could be string or object with .id field
model=$(echo "$input" | jq -r '
  if .model | type == "object" then .model.id // .model.name // "claude"
  elif .model | type == "string" then .model
  else "claude"
  end
' 2>/dev/null)
[ -z "$model" ] || [ "$model" = "null" ] && model="claude"
# Clean up model name - remove claude- prefix and date suffix, truncate
model=$(echo "$model" | sed 's/claude-//' | sed 's/-[0-9]*$//' | cut -c1-10)

# ANSI color codes (using $'...' for proper escape handling)
RESET=$'\033[0m'
BG_BLUE=$'\033[44m'
FG_BLUE=$'\033[34m'
BG_GREEN=$'\033[42m'
FG_GREEN=$'\033[32m'
BG_YELLOW=$'\033[43m'
FG_YELLOW=$'\033[33m'
BG_CYAN=$'\033[46m'
FG_CYAN=$'\033[36m'
BG_RED=$'\033[41m'
FG_RED=$'\033[31m'
BG_ORANGE=$'\033[48;5;208m'
FG_ORANGE=$'\033[38;5;208m'
BG_MAGENTA=$'\033[45m'
FG_MAGENTA=$'\033[35m'
FG_BLACK=$'\033[30m'
FG_WHITE=$'\033[97m'
BOLD=$'\033[1m'
BLINK=$'\033[5m'

# Powerline separator
SEP=''

# --- Helpers for the second (limits) line ---

# Format a Unix-epoch reset time as a compact countdown, e.g. "2h13m", "4d6h".
fmt_reset() {
    local target=$1 now delta d h m
    now=$(date +%s)
    delta=$((target - now))
    [ "$delta" -le 0 ] && { echo "now"; return; }
    d=$((delta / 86400))
    h=$(((delta % 86400) / 3600))
    m=$(((delta % 3600) / 60))
    if [ "$d" -gt 0 ]; then echo "${d}d${h}h"
    elif [ "$h" -gt 0 ]; then echo "${h}h${m}m"
    else echo "${m}m"; fi
}

# Build a 10-char color-coded progress bar for an integer percentage (0-100).
# Used for every bar on line 2 (ctx, 5h, 7d), color-coded by usage level.
build_bar() {
    local pct=$1 bar_width=10 filled empty fill_color blink="" empty_color
    local fb="" eb="" i
    filled=$((pct * bar_width / 100))
    [ "$filled" -gt "$bar_width" ] && filled=$bar_width
    [ "$filled" -lt 0 ] && filled=0
    empty=$((bar_width - filled))
    if [ "$pct" -gt 95 ]; then fill_color=$'\033[38;5;196m'; blink=$BLINK
    elif [ "$pct" -gt 85 ]; then fill_color=$'\033[38;5;208m'
    elif [ "$pct" -gt 70 ]; then fill_color=$'\033[38;5;220m'
    else fill_color=$'\033[38;5;29m'; fi
    empty_color=$'\033[38;5;240m'
    for ((i=0; i<filled; i++)); do fb+="█"; done
    for ((i=0; i<empty; i++)); do eb+="░"; done
    printf '%s' "${blink}${fill_color}${fb}${RESET}${empty_color}${eb}${RESET}"
}

# Git info - check status first to determine model background color
git_segment=""
model_bg=$BG_GREEN  # default to green
model_fg=$FG_GREEN
if git -C "$cwd" rev-parse --git-dir > /dev/null 2>&1; then
    branch=$(git -C "$cwd" branch --show-current 2>/dev/null)
    [ -z "$branch" ] && branch=$(git -C "$cwd" rev-parse --short HEAD 2>/dev/null)

    # Get status counts (filter empty lines to avoid false counts)
    status=$(git -C "$cwd" status --porcelain 2>/dev/null)
    staged=$(echo "$status" | grep -c '^[MADRC]' || true)
    modified=$(echo "$status" | grep -c '^.[MD]' || true)
    conflicts=$(echo "$status" | grep -c '^[UDA][UDA]' || true)

    # Ahead/behind
    ahead=$(git -C "$cwd" rev-list --count @{u}..HEAD 2>/dev/null || echo 0)
    behind=$(git -C "$cwd" rev-list --count HEAD..@{u} 2>/dev/null || echo 0)

    # Build compact git status (p10k style)
    git_status=""
    [ "$ahead" -gt 0 ] 2>/dev/null && git_status+="⇡$ahead"
    [ "$behind" -gt 0 ] 2>/dev/null && git_status+="⇣$behind"
    [ "$conflicts" -gt 0 ] && git_status+="~$conflicts"
    [ "$staged" -gt 0 ] && git_status+="+$staged"
    [ "$modified" -gt 0 ] && git_status+="!$modified"

    # Choose color - light blue for branch, green/yellow for model based on status
    BG_LTBLUE=$'\033[48;5;75m'
    FG_LTBLUE=$'\033[38;5;75m'
    if [ -n "$git_status" ]; then
        git_bg=$BG_LTBLUE
        git_fg=$FG_LTBLUE
        model_bg=$BG_YELLOW
        model_fg=$FG_YELLOW
        git_content=" $branch $git_status "
    else
        git_bg=$BG_LTBLUE
        git_fg=$FG_LTBLUE
        model_bg=$BG_GREEN
        model_fg=$FG_GREEN
        git_content=" $branch "
    fi
    git_segment="${FG_BLUE}${git_bg}${SEP}${FG_BLACK}${git_content}"
    next_fg=$git_fg
    next_bg=$BG_CYAN
else
    next_fg=$FG_BLUE
    next_bg=$BG_CYAN
fi

# Context window usage -- rendered on line 2 (built-in since Claude Code 2.1.6+).
ctx_pct=""
ctx_raw=$(echo "$input" | jq -r '.context_window.used_percentage // empty' 2>/dev/null)
[ -n "$ctx_raw" ] && [ "$ctx_raw" != "null" ] && ctx_pct=$(printf '%.0f' "$ctx_raw" 2>/dev/null)

# Date and time
current_datetime=$(date +"%Y/%m/%d %H:%M")

# --- Line 2: context usage + session (5h) / weekly (7d) limit usage ---
DIM=$'\033[38;5;244m'
LABEL=$'\033[38;5;250m'

# Context segment (always shown; falls back to --% before the first response).
if [ -n "$ctx_pct" ]; then
    ctx_segment="${LABEL}ctx ${RESET}$(build_bar "$ctx_pct")${FG_WHITE} ${ctx_pct}%${RESET}"
else
    ctx_segment="${LABEL}ctx ${DIM}--%${RESET}"
fi

# Session / weekly limit segments. rate_limits is present only for Claude.ai
# Pro/Max subscribers, after the first API response; each window may be absent.
limit_segment=""
five_pct_raw=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty' 2>/dev/null)
five_reset_raw=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty' 2>/dev/null)
seven_pct_raw=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty' 2>/dev/null)
seven_reset_raw=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty' 2>/dev/null)

if [ -n "$five_pct_raw" ]; then
    five_pct=$(printf '%.0f' "$five_pct_raw" 2>/dev/null)
    seg="${LABEL}5h ${RESET}$(build_bar "$five_pct")${FG_WHITE} ${five_pct}%${RESET}"
    [ -n "$five_reset_raw" ] && seg+="${DIM} ↻$(fmt_reset "$five_reset_raw")${RESET}"
    limit_segment="$seg"
fi

if [ -n "$seven_pct_raw" ]; then
    seven_pct=$(printf '%.0f' "$seven_pct_raw" 2>/dev/null)
    seg="${LABEL}7d ${RESET}$(build_bar "$seven_pct")${FG_WHITE} ${seven_pct}%${RESET}"
    [ -n "$seven_reset_raw" ] && seg+="${DIM} ↻$(fmt_reset "$seven_reset_raw")${RESET}"
    [ -n "$limit_segment" ] && limit_segment+="   "
    limit_segment+="$seg"
fi

# Combine: ctx always, limits appended when present.
line2="$ctx_segment"
[ -n "$limit_segment" ] && line2+="   $limit_segment"

# Build output with powerline style
# Line 1: model / directory / git / date-time
# Model: black on green (clean) or yellow (dirty)
echo -n "${model_bg}${FG_BLACK}${BOLD} $model ${RESET}"
echo -n "${model_fg}${BG_BLUE}${SEP}${FG_BLACK}  $dir_name ${RESET}"
echo -n "$git_segment"
echo -n "${next_fg}${RESET}${SEP} ${current_datetime}"

# Line 2: context usage, then session/weekly limit usage when available.
printf '\n'
echo -n " ${line2}"
