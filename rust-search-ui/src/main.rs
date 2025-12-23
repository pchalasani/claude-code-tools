//! Rust TUI for session search - closely modeled after zippoxer/recall
//!
//! Features:
//! - Search bar at top with scope indicator
//! - Session list with project, agent, time ago, snippet
//! - Preview pane with conversation messages
//! - Keyboard shortcuts for navigation and actions

use anyhow::{Context, Result};
use chrono::{DateTime, Local, TimeZone, Utc};
use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind, KeyModifiers},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span},
    widgets::{List, ListItem, ListState, Paragraph},
    Frame, Terminal,
};
use serde::Serialize;
use std::io::{self, stdout};
use std::time::Duration;
use std::collections::{HashMap, HashSet};
use tantivy::{
    collector::TopDocs,
    query::{AllQuery, BooleanQuery, BoostQuery, Occur, PhraseQuery, QueryParser, TermQuery},
    schema::{IndexRecordOption, Value},
    snippet::SnippetGenerator,
    Index, ReloadPolicy, Term,
};

// ============================================================================
// Theme
// ============================================================================

struct Theme {
    selection_bg: Color,
    selection_header_fg: Color,
    selection_snippet_fg: Color,
    snippet_fg: Color,
    match_fg: Color,
    search_bg: Color,
    placeholder_fg: Color,
    accent: Color,
    dim_fg: Color,
    keycap_bg: Color,
    user_bubble_bg: Color,
    user_label: Color,
    claude_bubble_bg: Color,
    codex_bubble_bg: Color,
    claude_source: Color,
    codex_source: Color,
    separator_fg: Color,
    scope_label_fg: Color,
}

impl Theme {
    fn dark() -> Self {
        Self {
            selection_bg: Color::Rgb(50, 50, 55),
            selection_header_fg: Color::Cyan,
            selection_snippet_fg: Color::Rgb(180, 180, 180),
            snippet_fg: Color::Rgb(120, 120, 120),
            match_fg: Color::Yellow,
            search_bg: Color::Rgb(30, 30, 35),
            placeholder_fg: Color::Rgb(100, 100, 100),
            accent: Color::Cyan,
            dim_fg: Color::Rgb(100, 100, 100),
            keycap_bg: Color::Rgb(60, 60, 65),
            user_bubble_bg: Color::Rgb(30, 45, 55),
            user_label: Color::Rgb(80, 180, 220),
            claude_bubble_bg: Color::Rgb(45, 35, 30),
            codex_bubble_bg: Color::Rgb(30, 45, 35),
            claude_source: Color::Rgb(255, 150, 50),
            codex_source: Color::Rgb(80, 200, 120),
            separator_fg: Color::Rgb(60, 60, 65),
            scope_label_fg: Color::Rgb(140, 140, 140),
        }
    }
}

// ============================================================================
// Terminal Size Constants
// ============================================================================

/// Minimum terminal width to properly display all session fields without truncation.
/// Fields: row# + session_id + project + branch + lines + date + annotations
const MIN_TERMINAL_WIDTH: u16 = 110;

// ============================================================================
// Session Data
// ============================================================================

#[derive(Debug, Clone, Serialize)]
struct Session {
    session_id: String,
    agent: String,
    project: String,
    branch: String,
    cwd: String,
    created: String,
    modified: String,
    modified_ts: u64,         // Epoch milliseconds for reliable sorting
    lines: i64,
    #[serde(rename = "file_path")]
    export_path: String,
    first_msg_role: String,
    first_msg_content: String,
    last_msg_role: String,
    last_msg_content: String,
    derivation_type: String,  // "trimmed", "continued", or ""
    is_sidechain: bool,       // Sub-agent session
    claude_home: String,      // Source Claude home directory
    custom_title: String,     // User-assigned session name (from /rename)
}

impl Session {
    fn project_name(&self) -> &str {
        if self.project.is_empty() {
            std::path::Path::new(&self.cwd)
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("unknown")
        } else {
            &self.project
        }
    }

    fn agent_icon(&self) -> &str {
        if self.agent == "claude" {
            "●"
        } else {
            "■"
        }
    }

    fn agent_display(&self) -> &str {
        if self.agent == "claude" {
            "Claude"
        } else {
            "Codex"
        }
    }

    fn time_ago(&self) -> String {
        format_time_ago(&self.modified)
    }

    /// Session ID display with annotations: abc12345 (t) (r) (s)
    /// For Codex, extracts UUID (last 36 chars) from session_id
    fn session_id_display(&self) -> String {
        // UUIDs are always 36 characters (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
        // For Codex, extract last 36 chars which is the UUID
        let clean_id = if self.agent == "codex" && self.session_id.len() >= 36 {
            &self.session_id[self.session_id.len() - 36..]
        } else {
            &self.session_id
        };

        let id_prefix = if clean_id.len() >= 8 {
            &clean_id[..8]
        } else {
            clean_id
        };
        let mut display = id_prefix.to_string();

        if self.derivation_type == "trimmed" {
            display.push_str(" (t)");
        } else if self.derivation_type == "continued" {
            // "continued" internally = "rolled-over" in UI, shown as (r)
            display.push_str(" (r)");
        }
        if self.is_sidechain {
            display.push_str(" (s)");
        }
        display
    }

    /// Branch display with fallback
    fn branch_display(&self) -> &str {
        if self.branch.is_empty() {
            "N/A"
        } else {
            &self.branch
        }
    }

    /// Date display as range: "11/27 - 11/29 15:23" or "11/29 15:23" if same day
    fn date_display(&self) -> String {
        // Parse timestamp and convert to local time for display
        let parse_date_local = |s: &str| -> Option<DateTime<Local>> {
            DateTime::parse_from_rfc3339(s)
                .or_else(|_| {
                    chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f")
                        .map(|ndt| Utc.from_utc_datetime(&ndt).fixed_offset())
                })
                .ok()
                .map(|dt| dt.with_timezone(&Local))
        };

        let modified_dt = parse_date_local(&self.modified);
        let created_dt = parse_date_local(&self.created);

        match (created_dt, modified_dt) {
            (Some(created), Some(modified)) => {
                // Ensure earlier date comes first (handle data inconsistencies)
                let (earlier, later) = if created <= modified {
                    (created, modified)
                } else {
                    (modified, created)
                };

                // Check if same day (in local time)
                if earlier.format("%m/%d").to_string() == later.format("%m/%d").to_string() {
                    // Same day: just show "11/29 15:23" (use the later/modified timestamp)
                    later.format("%m/%d %H:%M").to_string()
                } else {
                    // Different days: show range "11/27 - 11/29 15:23"
                    // Earlier date without time, later date with time
                    format!(
                        "{} - {}",
                        earlier.format("%m/%d"),
                        later.format("%m/%d %H:%M")
                    )
                }
            }
            (None, Some(modified)) => modified.format("%m/%d %H:%M").to_string(),
            _ => self.modified.clone(),
        }
    }

    /// Medium date display: "11/27 - 11/29" or "11/29" (no time)
    fn date_medium(&self) -> String {
        // Parse timestamp and convert to local time for display
        let parse_date_local = |s: &str| -> Option<DateTime<Local>> {
            DateTime::parse_from_rfc3339(s)
                .or_else(|_| {
                    chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f")
                        .map(|ndt| Utc.from_utc_datetime(&ndt).fixed_offset())
                })
                .ok()
                .map(|dt| dt.with_timezone(&Local))
        };

        let modified_dt = parse_date_local(&self.modified);
        let created_dt = parse_date_local(&self.created);

        match (created_dt, modified_dt) {
            (Some(created), Some(modified)) => {
                let (earlier, later) = if created <= modified {
                    (created, modified)
                } else {
                    (modified, created)
                };

                if earlier.format("%m/%d").to_string() == later.format("%m/%d").to_string() {
                    later.format("%m/%d").to_string()
                } else {
                    format!("{} - {}", earlier.format("%m/%d"), later.format("%m/%d"))
                }
            }
            (None, Some(modified)) => modified.format("%m/%d").to_string(),
            _ => self.modified.chars().take(5).collect(),
        }
    }

    /// Compact date display: relative time like "3h", "5d", "2w", "3mo"
    fn date_compact(&self) -> String {
        let parse_date = |s: &str| {
            DateTime::parse_from_rfc3339(s)
                .or_else(|_| {
                    chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f")
                        .map(|ndt| Utc.from_utc_datetime(&ndt).fixed_offset())
                })
                .ok()
        };

        let modified_dt = match parse_date(&self.modified) {
            Some(dt) => dt,
            None => return "?".to_string(),
        };

        let now = Utc::now();
        let duration = now.signed_duration_since(modified_dt);

        let hours = duration.num_hours();
        let days = duration.num_days();

        if hours < 1 {
            format!("{}m", duration.num_minutes().max(1))
        } else if hours < 24 {
            format!("{}h", hours)
        } else if days < 7 {
            format!("{}d", days)
        } else if days < 30 {
            format!("{}w", days / 7)
        } else if days < 365 {
            format!("{}mo", days / 30)
        } else {
            format!("{}y", days / 365)
        }
    }
}

// ============================================================================
// App State
// ============================================================================

struct App {
    sessions: Vec<Session>,
    filtered: Vec<usize>, // Indices into sessions
    query: String,
    selected: usize,
    list_scroll: usize,
    preview_scroll: usize,
    should_quit: bool,
    should_select: Option<Session>,
    total_sessions: usize,
    scope_global: bool,
    launch_cwd: String,
    index_path: String, // Path to Tantivy index for keyword search
    search_snippets: HashMap<String, String>, // session_id -> matching snippet from content

    // Filter state - inclusion-based (true = include this type)
    include_original: bool,   // true by default - include original sessions
    include_sub: bool,        // false by default - exclude sub-agents
    include_trimmed: bool,    // true by default - include trimmed sessions
    include_continued: bool,  // true by default - include continued sessions
    filter_agent: Option<String>, // None = all, Some("claude"), Some("codex")
    filter_min_lines: Option<i64>,
    filter_after_date: Option<String>,  // YYYYMMDD - modified date must be >= this
    filter_after_date_display: Option<String>, // User-friendly display format
    filter_before_date: Option<String>, // YYYYMMDD - modified date must be <= this
    filter_before_date_display: Option<String>, // User-friendly display format
    filter_claude_home: Option<String>, // Filter to sessions from this Claude home
    filter_codex_home: Option<String>,  // Filter Codex sessions to this Codex home

    // Command mode (: prefix)
    command_mode: bool,

    // Full conversation view
    full_view_mode: bool,
    full_content: String,
    full_content_scroll: usize,

    // View mode search (/pattern like less)
    view_search_mode: bool,      // Entering search pattern
    view_search_pattern: String, // Current search pattern
    view_search_matches: Vec<usize>, // Line numbers with matches
    view_search_current: usize,  // Current match index

    // Original query match navigation (blue highlights)
    query_match_lines: Vec<usize>,  // Line numbers with original query matches
    query_match_current: usize,     // Current match index for query navigation
    query_nav_mode: bool,           // True when navigating original query matches (empty / search)

    // Jump mode (num+Enter)
    jump_input: String,

    // Input mode for :m and :a
    input_mode: Option<InputMode>,
    input_buffer: String,

    // Action mode for Enter (view/actions)
    action_mode: Option<ActionMode>,
    action_modal_selected: usize,
    selected_action: Option<String>,

    // Filter modal
    filter_modal_open: bool,
    filter_modal_selected: usize,

    // Scope modal (/ key)
    scope_modal_open: bool,
    scope_modal_selected: usize,
    filter_dir: Option<String>, // Custom directory filter (overrides scope_global)

    // Branch filter (Ctrl+B) - only effective when not in global mode
    filter_branch: Option<String>,
    launch_branch: String, // Current git branch at launch (for default value)

    // Result limit
    max_results: Option<usize>, // Limit number of displayed results (--num-results / -n)

    // Sort mode: false = relevance (default), true = time (reverse chronological)
    sort_by_time: bool,

    // Exit confirmation
    confirming_exit: bool,
    // Delete confirmation
    confirming_delete: bool,

    // Temporary status message (e.g., "Copied to clipboard")
    status_message: Option<String>,
}

#[derive(Clone, PartialEq)]
enum InputMode {
    MinLines,   // :m - waiting for number
    Agent,      // :a - waiting for 1 or 2
    JumpToLine, // C-g - waiting for line number
    AfterDate,  // :> - waiting for date
    BeforeDate, // :< - waiting for date
    ScopeDir,   // Custom directory for scope filter
    Branch,     // C-b - waiting for branch name
}

#[derive(Clone, PartialEq)]
enum ActionMode {
    ActionMenu,  // User pressed Enter, showing flattened action menu
}

#[derive(Clone, PartialEq)]
enum FilterMenuItem {
    ClearAll,
    IncludeOriginal,
    IncludeSub,
    IncludeTrimmed,
    IncludeContinued,  // Internally "continued", displayed as "rollover" to user
    AgentAll,
    AgentClaude,
    AgentCodex,
    MinLines,
    AfterDate,
    BeforeDate,
}

impl FilterMenuItem {
    fn all() -> Vec<FilterMenuItem> {
        vec![
            FilterMenuItem::ClearAll,
            FilterMenuItem::IncludeOriginal,
            FilterMenuItem::IncludeSub,
            FilterMenuItem::IncludeTrimmed,
            FilterMenuItem::IncludeContinued,
            FilterMenuItem::AgentAll,
            FilterMenuItem::AgentClaude,
            FilterMenuItem::AgentCodex,
            FilterMenuItem::MinLines,
            FilterMenuItem::AfterDate,
            FilterMenuItem::BeforeDate,
        ]
    }

    fn label(&self) -> &str {
        match self {
            FilterMenuItem::ClearAll => "(x) Reset to defaults",
            FilterMenuItem::IncludeOriginal => "(o) Include original sessions",
            FilterMenuItem::IncludeSub => "(s) Include sub-agent sessions",
            FilterMenuItem::IncludeTrimmed => "(t) Include trimmed sessions",
            FilterMenuItem::IncludeContinued => "(r) Include rollover sessions",
            FilterMenuItem::AgentAll => "(a) All agents",
            FilterMenuItem::AgentClaude => "(d) Claude only",
            FilterMenuItem::AgentCodex => "(e) Codex only",
            FilterMenuItem::MinLines => "(l) Minimum lines",
            FilterMenuItem::AfterDate => "(>) After date",
            FilterMenuItem::BeforeDate => "(<) Before date",
        }
    }

    fn shortcut(&self) -> char {
        match self {
            FilterMenuItem::ClearAll => 'x',
            FilterMenuItem::IncludeOriginal => 'o',
            FilterMenuItem::IncludeSub => 's',
            FilterMenuItem::IncludeTrimmed => 't',
            FilterMenuItem::IncludeContinued => 'r',
            FilterMenuItem::AgentAll => 'a',
            FilterMenuItem::AgentClaude => 'd',
            FilterMenuItem::AgentCodex => 'e',
            FilterMenuItem::MinLines => 'l',
            FilterMenuItem::AfterDate => '>',
            FilterMenuItem::BeforeDate => '<',
        }
    }
}

#[derive(Clone, PartialEq)]
enum ActionMenuItem {
    View,       // (v) View full session - handled in Rust
    Path,       // (p) Show session file path
    Copy,       // (c) Copy session file
    CopyId,     // (i) Copy session ID to clipboard - handled in Rust
    Export,     // (e) Export to text file (.txt)
    Query,      // (q) Query the session
    Resume,     // (r) Resume as-is
    Clone,      // (l) Clone session + resume clone
    Trim,       // (t) Trim + resume
    SmartTrim,  // (s) Smart trim + resume
    Continue,   // (o) Rollover - internally "continue", displayed as "rollover" to user
    Delete,     // (d) Delete session file (with confirmation)
}

impl ActionMenuItem {
    fn all() -> Vec<ActionMenuItem> {
        vec![
            ActionMenuItem::View,
            ActionMenuItem::Path,
            ActionMenuItem::Copy,
            ActionMenuItem::CopyId,
            ActionMenuItem::Export,
            ActionMenuItem::Query,
            ActionMenuItem::Resume,
            ActionMenuItem::Clone,
            ActionMenuItem::Trim,
            ActionMenuItem::SmartTrim,
            ActionMenuItem::Continue,
            ActionMenuItem::Delete,
        ]
    }

    fn label(&self) -> &str {
        match self {
            ActionMenuItem::View => "(v) View full session",
            ActionMenuItem::Path => "(p) Show session file path",
            ActionMenuItem::Copy => "(c) Copy session file",
            ActionMenuItem::CopyId => "(i) Copy session ID to clipboard",
            ActionMenuItem::Export => "(e) Export to text file (.txt)",
            ActionMenuItem::Query => "(q) Query the session",
            ActionMenuItem::Resume => "(r) Resume as-is",
            ActionMenuItem::Clone => "(l) Clone session + resume clone",
            ActionMenuItem::Trim => "(t) Trim + resume...",
            ActionMenuItem::SmartTrim => "(s) Smart trim + resume...",
            ActionMenuItem::Continue => "(o) Rollover: handoff work to fresh session...",
            ActionMenuItem::Delete => "(d) Delete session",
        }
    }

    fn shortcut(&self) -> char {
        match self {
            ActionMenuItem::View => 'v',
            ActionMenuItem::Path => 'p',
            ActionMenuItem::Copy => 'c',
            ActionMenuItem::CopyId => 'i',
            ActionMenuItem::Export => 'e',
            ActionMenuItem::Query => 'q',
            ActionMenuItem::Resume => 'r',
            ActionMenuItem::Clone => 'l',
            ActionMenuItem::Trim => 't',
            ActionMenuItem::SmartTrim => 's',
            ActionMenuItem::Continue => 'o',
            ActionMenuItem::Delete => 'd',
        }
    }

    /// Returns the action string to pass to Python handler.
    /// Note: "continue" is the internal name for what users see as "rollover".
    fn action_string(&self) -> &str {
        match self {
            ActionMenuItem::View => "view",
            ActionMenuItem::Path => "path",
            ActionMenuItem::Copy => "copy",
            ActionMenuItem::CopyId => "copy_id",  // Handled in Rust
            ActionMenuItem::Export => "export",
            ActionMenuItem::Query => "query",
            ActionMenuItem::Resume => "resume",
            ActionMenuItem::Clone => "clone",
            ActionMenuItem::Trim => "suppress_resume",
            ActionMenuItem::SmartTrim => "smart_trim_resume",
            ActionMenuItem::Continue => "continue",  // "rollover" in UI
            ActionMenuItem::Delete => "delete",
        }
    }

    /// Returns true if this action launches a new session (no pop-back)
    fn is_launch_action(&self) -> bool {
        matches!(
            self,
            ActionMenuItem::Resume
                | ActionMenuItem::Clone
                | ActionMenuItem::Trim
                | ActionMenuItem::SmartTrim
                | ActionMenuItem::Continue
        )
    }
}

/// Get the current git branch name, or empty string if not in a git repo
fn get_current_git_branch() -> String {
    std::process::Command::new("git")
        .args(["rev-parse", "--abbrev-ref", "HEAD"])
        .output()
        .ok()
        .and_then(|output| {
            if output.status.success() {
                String::from_utf8(output.stdout)
                    .ok()
                    .map(|s| s.trim().to_string())
            } else {
                None
            }
        })
        .unwrap_or_default()
}

impl App {
    fn new(sessions: Vec<Session>, index_path: String, filter_claude_home: Option<String>, filter_codex_home: Option<String>) -> Self {
        let total = sessions.len();
        let launch_cwd = std::env::current_dir()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let launch_branch = get_current_git_branch();

        let mut app = Self {
            sessions,
            filtered: Vec::new(),
            query: String::new(),
            selected: 0,
            list_scroll: 0,
            preview_scroll: 0,
            should_quit: false,
            should_select: None,
            total_sessions: total,
            scope_global: false,
            launch_cwd,
            index_path,
            search_snippets: HashMap::new(),
            // Filter state
            include_original: true,   // Include original by default
            include_sub: false,       // Exclude sub-agents by default
            include_trimmed: true,    // Include trimmed by default
            include_continued: true,  // Include continued by default
            filter_agent: None,
            filter_min_lines: None,
            filter_after_date: None,
            filter_after_date_display: None,
            filter_before_date: None,
            filter_before_date_display: None,
            filter_claude_home,
            filter_codex_home,
            // Command mode
            command_mode: false,
            // Full view mode
            full_view_mode: false,
            full_content: String::new(),
            full_content_scroll: 0,
            // View mode search
            view_search_mode: false,
            view_search_pattern: String::new(),
            view_search_matches: Vec::new(),
            view_search_current: 0,
            // Original query match navigation
            query_match_lines: Vec::new(),
            query_match_current: 0,
            query_nav_mode: false,
            // Jump mode
            jump_input: String::new(),
            // Input mode
            input_mode: None,
            input_buffer: String::new(),
            // Action mode
            action_mode: None,
            action_modal_selected: 0,
            selected_action: None,
            // Filter modal
            filter_modal_open: false,
            filter_modal_selected: 0,
            // Scope modal
            scope_modal_open: false,
            scope_modal_selected: 0,
            filter_dir: None,
            // Branch filter
            filter_branch: None,
            launch_branch,
            // Result limit
            max_results: None,
            // Sort mode
            sort_by_time: false,
            // Exit confirmation
            confirming_exit: false,
            // Delete confirmation
            confirming_delete: false,
            // Status message
            status_message: None,
        };
        app.filter();
        app
    }

    fn new_with_options(sessions: Vec<Session>, index_path: String, cli: &CliOptions) -> Self {
        let total = sessions.len();
        let launch_cwd = std::env::current_dir()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_default();
        let launch_branch = get_current_git_branch();

        // Parse date filters if provided
        let (after_date, after_display) = cli.after_date.as_ref()
            .and_then(|d| parse_flexible_date(d))
            .map(|(cmp, disp)| (Some(cmp), Some(disp)))
            .unwrap_or((None, None));

        let (before_date, before_display) = cli.before_date.as_ref()
            .and_then(|d| parse_flexible_date(d))
            .map(|(cmp, disp)| (Some(cmp), Some(disp)))
            .unwrap_or((None, None));

        let mut app = Self {
            sessions,
            filtered: Vec::new(),
            query: cli.query.clone().unwrap_or_default(),
            selected: 0,
            list_scroll: 0,
            preview_scroll: 0,
            should_quit: false,
            should_select: None,
            total_sessions: total,
            // --dir overrides -g: if filter_dir is set, scope_global is effectively false
            scope_global: if cli.filter_dir.is_some() { false } else { cli.global_search },
            launch_cwd,
            index_path,
            search_snippets: HashMap::new(),
            // Filter state from CLI
            // Defaults: show original + trimmed + continued (not sub-agents)
            // Subtractive flags (--no-*) exclude types from defaults
            // Additive flag (--sub-agent) adds sub-agents to defaults
            include_original: !cli.no_original,
            include_sub: cli.include_sub,
            include_trimmed: !cli.no_trimmed,
            include_continued: !cli.no_rollover,
            filter_agent: cli.agent_filter.clone(),
            filter_min_lines: cli.min_lines,
            filter_after_date: after_date,
            filter_after_date_display: after_display,
            filter_before_date: before_date,
            filter_before_date_display: before_display,
            filter_claude_home: cli.claude_home.clone(),
            filter_codex_home: cli.codex_home.clone(),
            // Command mode
            command_mode: false,
            // Full view mode
            full_view_mode: false,
            full_content: String::new(),
            full_content_scroll: 0,
            // View mode search
            view_search_mode: false,
            view_search_pattern: String::new(),
            view_search_matches: Vec::new(),
            view_search_current: 0,
            // Original query match navigation
            query_match_lines: Vec::new(),
            query_match_current: 0,
            query_nav_mode: false,
            // Jump mode
            jump_input: String::new(),
            // Input mode
            input_mode: None,
            input_buffer: String::new(),
            // Action mode
            action_mode: None,
            action_modal_selected: 0,
            selected_action: None,
            // Filter modal
            filter_modal_open: false,
            filter_modal_selected: 0,
            // Scope modal
            scope_modal_open: false,
            scope_modal_selected: 0,
            filter_dir: cli.filter_dir.clone(),
            // Branch filter
            filter_branch: cli.filter_branch.clone(),
            launch_branch,
            // Result limit
            max_results: cli.num_results,
            // Sort mode (--by-time sorts by last-modified, default is relevance)
            sort_by_time: cli.sort_by_time,
            // Exit confirmation
            confirming_exit: false,
            // Delete confirmation
            confirming_delete: false,
            // Status message
            status_message: None,
        };
        app.filter();

        // Restore scroll/selection state from CLI if provided
        // Must be done after filter() populates the filtered list
        if let Some(sel) = cli.selected {
            // Clamp to valid range
            if !app.filtered.is_empty() {
                app.selected = sel.min(app.filtered.len() - 1);
            }
        }
        if let Some(scroll) = cli.list_scroll {
            // Clamp to valid range
            if !app.filtered.is_empty() {
                app.list_scroll = scroll.min(app.filtered.len().saturating_sub(1));
            }
        }

        app
    }

    fn filter(&mut self) {
        self.filtered = self
            .sessions
            .iter()
            .enumerate()
            .filter(|(_, s)| {
                // Home filter - apply based on session agent type
                if s.agent == "codex" {
                    // Codex session: filter by codex_home
                    if let Some(ref codex_home) = self.filter_codex_home {
                        if !s.claude_home.is_empty() && s.claude_home != *codex_home {
                            return false;
                        }
                    }
                } else {
                    // Claude session: filter by claude_home
                    if let Some(ref home) = self.filter_claude_home {
                        if !s.claude_home.is_empty() && s.claude_home != *home {
                            return false;
                        }
                    }
                }

                // Scope filter: filter_dir overrides scope_global
                if let Some(ref filter_dir) = self.filter_dir {
                    // Custom directory filter - match exact dir or subdirectories
                    // Must be exact match OR start with filter_dir + "/"
                    if !s.cwd.is_empty() {
                        let is_match = s.cwd == *filter_dir
                            || s.cwd.starts_with(&format!("{}/", filter_dir));
                        if !is_match {
                            return false;
                        }
                    }
                } else if !self.scope_global && !s.cwd.is_empty() && s.cwd != self.launch_cwd {
                    return false;
                }

                // Inclusion-based filtering: check if session type is included

                // Sub-agent sessions are handled separately from derivation type
                if s.is_sidechain {
                    // Sub-agent: include only if include_sub is true
                    // (derivation type filter does NOT apply to sub-agents)
                    if !self.include_sub {
                        return false;
                    }
                } else {
                    // Non-sub-agent: apply derivation type filter
                    let derivation_included = match s.derivation_type.as_str() {
                        "" => self.include_original,           // Original session
                        "trimmed" => self.include_trimmed,     // Trimmed session
                        "continued" => self.include_continued, // Continued session
                        _ => true, // Unknown type, include by default
                    };
                    if !derivation_included {
                        return false;
                    }
                }

                // Agent filter
                if let Some(ref agent) = self.filter_agent {
                    if s.agent != *agent {
                        return false;
                    }
                }

                // Branch filter (only effective when not in global scope)
                if !self.scope_global {
                    if let Some(ref branch) = self.filter_branch {
                        if s.branch != *branch {
                            return false;
                        }
                    }
                }

                // Min lines filter
                if let Some(min) = self.filter_min_lines {
                    if s.lines < min {
                        return false;
                    }
                }

                // Date filters (applied to modified date)
                if let Some(ref after_date) = self.filter_after_date {
                    if let Some(session_date) = extract_date_for_comparison(&s.modified) {
                        if session_date < *after_date {
                            return false;
                        }
                    }
                }
                if let Some(ref before_date) = self.filter_before_date {
                    if let Some(session_date) = extract_date_for_comparison(&s.modified) {
                        if session_date > *before_date {
                            return false;
                        }
                    }
                }

                // No query filter at this stage - handled by tantivy_matches below
                true
            })
            .map(|(i, _)| i)
            .collect();

        // If there's a keyword query, use Tantivy full-text search
        if !self.query.trim().is_empty() {
            let (snippets, ranked_ids) = search_tantivy(
                &self.index_path,
                &self.query,
                self.filter_claude_home.as_deref(),
                self.filter_codex_home.as_deref(),
            );
            if !snippets.is_empty() {
                // Store snippets for rendering
                self.search_snippets = snippets.clone();
                // Filter to only sessions that match the Tantivy search
                self.filtered.retain(|&i| {
                    snippets.contains_key(&self.sessions[i].session_id)
                });

                if self.sort_by_time {
                    // Sort by modified_ts (numeric epoch ms, reverse chronological)
                    self.filtered.sort_by(|&a, &b| {
                        self.sessions[b].modified_ts.cmp(&self.sessions[a].modified_ts)
                    });
                } else {
                    // Reorder filtered by Tantivy ranking (phrase + recency boosted)
                    // Build position map for ranking
                    let rank_pos: HashMap<&str, usize> = ranked_ids
                        .iter()
                        .enumerate()
                        .map(|(pos, id)| (id.as_str(), pos))
                        .collect();

                    // Sort filtered by position in ranked_ids (lower = higher rank)
                    self.filtered.sort_by_key(|&i| {
                        rank_pos
                            .get(self.sessions[i].session_id.as_str())
                            .copied()
                            .unwrap_or(usize::MAX)
                    });
                }
            } else {
                // No Tantivy matches - clear results and snippets
                self.search_snippets.clear();
                self.filtered.clear();
            }
        } else {
            // Clear snippets when no query - sort by time (most recent first)
            self.search_snippets.clear();
            self.filtered.sort_by(|&a, &b| {
                self.sessions[b].modified_ts.cmp(&self.sessions[a].modified_ts)
            });
        }

        // Apply max_results limit if specified
        if let Some(limit) = self.max_results {
            self.filtered.truncate(limit);
        }

        self.selected = 0;
        self.list_scroll = 0;
        self.preview_scroll = 0;
    }

    fn selected_session(&self) -> Option<&Session> {
        self.filtered
            .get(self.selected)
            .map(|&i| &self.sessions[i])
    }

    fn on_char(&mut self, c: char) {
        self.query.push(c);
        self.filter();
    }

    fn on_backspace(&mut self) {
        self.query.pop();
        self.filter();
    }

    fn has_active_filters(&self) -> bool {
        !self.query.is_empty()
            || self.filter_min_lines.is_some()
            || self.filter_after_date.is_some()
            || self.filter_before_date.is_some()
            || self.filter_agent.is_some()
            || self.filter_branch.is_some()
            || !self.include_original
            || self.include_sub
            || !self.include_trimmed
            || !self.include_continued
    }

    fn on_escape(&mut self) {
        if self.query.is_empty() {
            // If there are active filters, show confirmation before exiting
            if self.has_active_filters() {
                self.confirming_exit = true;
            } else {
                self.should_quit = true;
            }
        } else {
            self.query.clear();
            self.filter();
        }
    }

    fn on_up(&mut self) {
        if !self.filtered.is_empty() {
            self.selected = self.selected.saturating_sub(1);
            self.preview_scroll = 0;
        }
    }

    fn on_down(&mut self) {
        if !self.filtered.is_empty() {
            self.selected = (self.selected + 1).min(self.filtered.len() - 1);
            self.preview_scroll = 0;
        }
    }

    fn page_up(&mut self, lines: usize) {
        if !self.filtered.is_empty() {
            self.selected = self.selected.saturating_sub(lines);
            self.preview_scroll = 0;
        }
    }

    fn page_down(&mut self, lines: usize) {
        if !self.filtered.is_empty() {
            self.selected = (self.selected + lines).min(self.filtered.len() - 1);
            self.preview_scroll = 0;
        }
    }

    fn on_enter(&mut self) {
        if let Some(session) = self.selected_session() {
            self.should_select = Some(session.clone());
            self.should_quit = true;
        }
    }

    fn toggle_scope(&mut self) {
        self.scope_global = !self.scope_global;
        self.filter();
    }

    fn scope_display(&self) -> String {
        // Determine which directory to display
        let dir_to_show = if let Some(ref dir) = self.filter_dir {
            dir.clone()
        } else if self.scope_global {
            return "everywhere".to_string();
        } else {
            self.launch_cwd.clone()
        };

        // Show ~/path for short paths, ~/.../<dir> for long paths
        let home = std::env::var("HOME").unwrap_or_default();
        let path = if !home.is_empty() && dir_to_show.starts_with(&home) {
            format!("~{}", &dir_to_show[home.len()..])
        } else {
            dir_to_show.clone()
        };
        if path.len() > 35 {
            let last = std::path::Path::new(&dir_to_show)
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("");
            format!("~/.../{}", last)
        } else {
            path
        }
    }

    fn scroll_preview_up(&mut self, lines: usize) {
        self.preview_scroll = self.preview_scroll.saturating_sub(lines);
    }

    fn scroll_preview_down(&mut self, lines: usize) {
        self.preview_scroll = self.preview_scroll.saturating_add(lines);
    }

    fn jump_to_row(&mut self, row: usize) {
        if row > 0 && row <= self.filtered.len() {
            self.selected = row - 1; // Convert 1-indexed to 0-indexed
            self.preview_scroll = 0;
        }
        self.jump_input.clear();
    }

    fn process_jump_enter(&mut self) {
        if let Ok(row) = self.jump_input.parse::<usize>() {
            self.jump_to_row(row);
        }
        self.jump_input.clear();
    }

    /// Check if any filtered session has annotations (c/t/sub)
    fn has_annotations(&self) -> bool {
        self.filtered.iter().any(|&idx| {
            let s = &self.sessions[idx];
            !s.derivation_type.is_empty() || s.is_sidechain
        })
    }

    /// Calculate minimum terminal width needed to display all fields without truncation.
    /// Based on the actual field widths in the current filtered results.
    fn min_width_for_full_display(&self) -> u16 {
        if self.filtered.is_empty() {
            return MIN_TERMINAL_WIDTH; // Fallback to default
        }

        // Calculate max widths for each field (same logic as render_session_list)
        let row_num_width = self.filtered.len().to_string().len().max(2);

        let mut max_session_id_len = 0usize;
        let mut max_project_len = 0usize;
        let mut max_branch_len = 0usize;
        let mut max_lines_len = 0usize;
        for &idx in &self.filtered {
            let s = &self.sessions[idx];
            max_session_id_len = max_session_id_len.max(s.session_id_display().len());
            max_project_len = max_project_len.max(s.project_name().len());
            max_branch_len = max_branch_len.max(s.branch_display().len());
            max_lines_len = max_lines_len.max(format!("{}L", s.lines).len());
        }
        // Apply same min/max constraints as render_session_list
        max_session_id_len = max_session_id_len.max(8).min(18);
        max_project_len = max_project_len.max(10).min(40);
        max_branch_len = max_branch_len.max(8).min(35);
        max_lines_len = max_lines_len.max(4);

        // Fixed overhead: row_num + space + icon/agent (8) + 4 separators (12) + padding (2)
        let fixed_overhead = row_num_width + 1 + 8 + 12 + 2;

        // Non-date width
        let non_date_width = fixed_overhead + max_session_id_len + max_project_len + max_branch_len + max_lines_len;

        // Full date format needs ~19 chars ("11/27 - 11/29 15:23")
        // Add some extra margin for the 70/30 split (list gets 70% of content area)
        // Content area is about (width - 4) for margins, list gets 70% of that
        // So we need: non_date_width + 19 = 0.7 * (width - 4)
        // => width = (non_date_width + 19) / 0.7 + 4
        let list_width_needed = non_date_width + 19;
        let total_width = ((list_width_needed as f64 / 0.7) as usize) + 6; // +6 for margins and rounding

        (total_width as u16).max(MIN_TERMINAL_WIDTH)
    }

    /// Update search matches for view mode search
    fn update_view_search_matches(&mut self) {
        self.view_search_matches.clear();
        self.view_search_current = 0;

        if self.view_search_pattern.is_empty() {
            return;
        }

        let pattern_lower = self.view_search_pattern.to_lowercase();
        for (i, line) in self.full_content.lines().enumerate() {
            if line.to_lowercase().contains(&pattern_lower) {
                self.view_search_matches.push(i);
            }
        }
    }

    /// Jump to next search match in view mode
    fn view_search_next(&mut self) {
        if self.view_search_matches.is_empty() {
            return;
        }

        // Move to next match index (wrap around if at end)
        self.view_search_current = (self.view_search_current + 1) % self.view_search_matches.len();
        self.full_content_scroll = self.view_search_matches[self.view_search_current];
    }

    /// Jump to previous search match in view mode
    fn view_search_prev(&mut self) {
        if self.view_search_matches.is_empty() {
            return;
        }

        // Move to previous match index (wrap around if at beginning)
        if self.view_search_current == 0 {
            self.view_search_current = self.view_search_matches.len() - 1;
        } else {
            self.view_search_current -= 1;
        }
        self.full_content_scroll = self.view_search_matches[self.view_search_current];
    }

    /// Jump to next original query match in view mode (blue highlights)
    fn query_match_next(&mut self) {
        if self.query_match_lines.is_empty() {
            return;
        }

        // Move to next match index (wrap around if at end)
        self.query_match_current = (self.query_match_current + 1) % self.query_match_lines.len();
        self.full_content_scroll = self.query_match_lines[self.query_match_current];
    }

    /// Jump to previous original query match in view mode (blue highlights)
    fn query_match_prev(&mut self) {
        if self.query_match_lines.is_empty() {
            return;
        }

        // Move to previous match index (wrap around if at beginning)
        if self.query_match_current == 0 {
            self.query_match_current = self.query_match_lines.len() - 1;
        } else {
            self.query_match_current -= 1;
        }
        self.full_content_scroll = self.query_match_lines[self.query_match_current];
    }
}

// ============================================================================
// UI Rendering
// ============================================================================

fn render(frame: &mut Frame, app: &mut App) {
    let t = Theme::dark();

    // Full view mode - take over entire screen
    if app.full_view_mode {
        render_full_conversation(frame, app, &t);
        return;
    }

    let area = frame.area();

    // Status bar height: 1 for shortcuts, +1 if we have annotations OR active filters
    let show_legend = app.has_annotations();
    let has_filters = !app.include_original
        || app.include_sub
        || !app.include_trimmed
        || !app.include_continued
        || app.filter_agent.is_some()
        || app.filter_min_lines.is_some()
        || app.filter_after_date.is_some()
        || app.filter_before_date.is_some();
    let status_height = if show_legend || has_filters { 2 } else { 1 };

    // Main layout
    let main_layout = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),            // Search bar
            Constraint::Length(1),            // Spacing
            Constraint::Min(0),               // Content
            Constraint::Length(1),            // Spacing
            Constraint::Length(status_height), // Status bar (+ legend if annotations)
        ])
        .split(area);

    // Search bar with margins
    let search_area = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Length(1),
            Constraint::Min(0),
            Constraint::Length(1),
        ])
        .split(main_layout[0]);

    render_search_bar(frame, app, &t, search_area[1]);

    // Content area with padding
    let content_area = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Length(1),
            Constraint::Min(0),
            Constraint::Length(1),
        ])
        .split(main_layout[2]);

    // Split content: 70% list, padding, 30% preview
    let content_layout = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(70),
            Constraint::Length(2),
            Constraint::Percentage(30),
        ])
        .split(content_area[1]);

    render_session_list(frame, app, &t, content_layout[0]);
    render_preview(frame, app, &t, content_layout[2]);

    // Status bar with padding
    let status_area = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Length(1),
            Constraint::Min(0),
            Constraint::Length(1),
        ])
        .split(main_layout[4]);

    render_status_bar(frame, app, &t, status_area[1], show_legend);

    // Terminal width warning (shown above status bar when too narrow)
    let min_width = app.min_width_for_full_display();
    if area.width < min_width {
        render_width_warning(frame, area, min_width, status_height);
    }

    // Filter modal overlay
    if app.filter_modal_open {
        render_filter_modal(frame, app, &t, area);
    }

    // Scope modal overlay
    if app.scope_modal_open {
        render_scope_modal(frame, app, &t, area);
    }

    // Action menu modal overlay
    if matches!(app.action_mode, Some(ActionMode::ActionMenu)) {
        render_action_modal(frame, app, &t, area);
    }

    // Exit confirmation modal overlay
    if app.confirming_exit {
        render_exit_confirmation_modal(frame, &t, area);
    }

    // Delete confirmation modal overlay
    if app.confirming_delete {
        render_delete_confirmation_modal(frame, app, &t, area);
    }
}

fn render_width_warning(frame: &mut Frame, area: Rect, min_width: u16, status_height: u16) {
    // Render a bright warning bar just above the status bar
    let warning_y = area.height.saturating_sub(status_height + 1);
    let warning_area = Rect::new(0, warning_y, area.width, 1);

    let msg = format!(
        " ⚠ Terminal too narrow ({} cols). Widen to {} cols for best display. ",
        area.width,
        min_width
    );

    let warning = Paragraph::new(Span::styled(
        msg,
        Style::default()
            .fg(Color::Black)
            .bg(Color::Yellow)
            .add_modifier(Modifier::BOLD),
    ));

    frame.render_widget(warning, warning_area);
}

fn render_exit_confirmation_modal(frame: &mut Frame, t: &Theme, area: Rect) {
    use ratatui::widgets::{Block, Borders, Clear};

    // Center the modal
    let modal_width = 52u16;
    let modal_height = 7u16; // message + 2 options + 2 border + 2 padding
    let x = (area.width.saturating_sub(modal_width)) / 2;
    let y = (area.height.saturating_sub(modal_height)) / 2;
    let modal_area = Rect::new(x, y, modal_width, modal_height);

    // Clear the area behind the modal
    frame.render_widget(Clear, modal_area);

    // Modal border
    let block = Block::default()
        .title(" Exit? ")
        .borders(Borders::ALL)
        .style(Style::default().bg(t.search_bg));
    frame.render_widget(block, modal_area);

    // Inner content area
    let inner = Rect::new(x + 2, y + 1, modal_width - 4, modal_height - 2);

    let keycap = Style::default().bg(t.keycap_bg);
    let label = Style::default();
    let dim = Style::default().fg(t.dim_fg);

    let lines = vec![
        Line::from(vec![
            Span::styled("You have active filters set.", dim),
        ]),
        Line::from(vec![]),
        Line::from(vec![
            Span::styled(" Enter ", keycap),
            Span::styled(" exit and lose filter settings", label),
        ]),
        Line::from(vec![
            Span::styled("  Esc  ", keycap),
            Span::styled(" cancel and return to search", label),
        ]),
    ];

    let paragraph = Paragraph::new(lines);
    frame.render_widget(paragraph, inner);
}

fn render_delete_confirmation_modal(frame: &mut Frame, app: &App, t: &Theme, area: Rect) {
    use ratatui::widgets::{Block, Borders, Clear};

    // Get session info for display
    let session_idx = app.filtered[app.selected];
    let session = &app.sessions[session_idx];
    let session_id = &session.session_id;
    let project = &session.project;
    let branch = &session.branch;
    let line_count = session.lines;

    // Center the modal
    let modal_width = 60u16;
    let modal_height = 10u16;
    let x = (area.width.saturating_sub(modal_width)) / 2;
    let y = (area.height.saturating_sub(modal_height)) / 2;
    let modal_area = Rect::new(x, y, modal_width, modal_height);

    // Clear the area behind the modal
    frame.render_widget(Clear, modal_area);

    // Modal border
    let block = Block::default()
        .title(" Delete Session? ")
        .borders(Borders::ALL)
        .style(Style::default().bg(t.search_bg));
    frame.render_widget(block, modal_area);

    // Inner content area
    let inner = Rect::new(x + 2, y + 1, modal_width - 4, modal_height - 2);

    let keycap = Style::default().bg(t.keycap_bg);
    let label = Style::default();
    let dim = Style::default().fg(t.dim_fg);
    let warn = Style::default().fg(Color::Red);

    // Build branch display (show branch if non-empty)
    let branch_span = if branch.is_empty() {
        Span::styled("—", dim)
    } else {
        Span::styled(branch.as_str(), label)
    };

    let lines = vec![
        Line::from(vec![
            Span::styled("Session: ", dim),
            Span::styled(&session_id[..12.min(session_id.len())], label),
            Span::styled("...  ", dim),
            Span::styled("Lines: ", dim),
            Span::styled(format!("{}", line_count), label),
        ]),
        Line::from(vec![
            Span::styled("Project: ", dim),
            Span::styled(project.as_str(), label),
            Span::styled("  Branch: ", dim),
            branch_span,
        ]),
        Line::from(vec![]),
        Line::from(vec![
            Span::styled("This action cannot be undone!", warn),
        ]),
        Line::from(vec![]),
        Line::from(vec![
            Span::styled(" Enter ", keycap),
            Span::styled(" confirm delete", label),
        ]),
        Line::from(vec![
            Span::styled("  Esc  ", keycap),
            Span::styled(" cancel", label),
        ]),
    ];

    let paragraph = Paragraph::new(lines);
    frame.render_widget(paragraph, inner);
}

fn render_action_modal(frame: &mut Frame, app: &App, t: &Theme, area: Rect) {
    use ratatui::widgets::{Block, Borders, Clear};

    // Center the modal - sized for 11 action items + Esc hint
    let modal_width = 54u16;
    let modal_height = 15u16; // 11 items + 1 hint + 2 border + 1 padding
    let x = (area.width.saturating_sub(modal_width)) / 2;
    let y = (area.height.saturating_sub(modal_height)) / 2;
    let modal_area = Rect::new(x, y, modal_width, modal_height);

    frame.render_widget(Clear, modal_area);

    let block = Block::default()
        .title(" Session Actions ")
        .borders(Borders::ALL)
        .style(Style::default().bg(t.search_bg));
    frame.render_widget(block, modal_area);

    let inner = Rect::new(x + 2, y + 1, modal_width - 4, modal_height - 2);

    let items = ActionMenuItem::all();
    let mut lines: Vec<Line> = Vec::new();

    for (i, item) in items.iter().enumerate() {
        let is_selected = i == app.action_modal_selected;
        let style = if is_selected {
            Style::default().bg(t.selection_bg).fg(t.selection_header_fg)
        } else {
            Style::default()
        };
        let prefix = if is_selected { "▶ " } else { "  " };
        lines.push(Line::from(vec![
            Span::styled(prefix, style),
            Span::styled(item.label(), style),
        ]));
    }

    // Add Esc hint at bottom
    lines.push(Line::from(vec![
        Span::styled("  ", Style::default()),
        Span::styled("Esc", Style::default().bg(t.keycap_bg)),
        Span::styled(" cancel", Style::default().fg(t.dim_fg)),
    ]));

    frame.render_widget(Paragraph::new(lines), inner);
}

fn render_filter_modal(frame: &mut Frame, app: &App, t: &Theme, area: Rect) {
    use ratatui::widgets::{Block, Borders, Clear};

    // Center the modal
    let modal_width = 42u16;
    let modal_height = 13u16; // 9 items + 2 border + 2 padding
    let x = (area.width.saturating_sub(modal_width)) / 2;
    let y = (area.height.saturating_sub(modal_height)) / 2;
    let modal_area = Rect::new(x, y, modal_width, modal_height);

    // Clear the area behind the modal
    frame.render_widget(Clear, modal_area);

    // Modal border
    let block = Block::default()
        .title(" Filters (|) ")
        .borders(Borders::ALL)
        .style(Style::default().bg(t.search_bg));
    frame.render_widget(block, modal_area);

    // Inner content area
    let inner = Rect::new(x + 2, y + 1, modal_width - 4, modal_height - 2);

    let items = FilterMenuItem::all();
    let mut lines: Vec<Line> = Vec::new();

    for (i, item) in items.iter().enumerate() {
        let is_selected = i == app.filter_modal_selected;

        // Show current state for toggleable filters
        let state_indicator = match item {
            FilterMenuItem::ClearAll => "".to_string(),
            FilterMenuItem::IncludeOriginal => if app.include_original { " [ON]" } else { " [off]" }.to_string(),
            FilterMenuItem::IncludeSub => if app.include_sub { " [ON]" } else { " [off]" }.to_string(),
            FilterMenuItem::IncludeTrimmed => if app.include_trimmed { " [ON]" } else { " [off]" }.to_string(),
            FilterMenuItem::IncludeContinued => if app.include_continued { " [ON]" } else { " [off]" }.to_string(),
            FilterMenuItem::AgentAll => if app.filter_agent.is_none() { " ●" } else { " ○" }.to_string(),
            FilterMenuItem::AgentClaude => if app.filter_agent.as_deref() == Some("claude") { " ●" } else { " ○" }.to_string(),
            FilterMenuItem::AgentCodex => if app.filter_agent.as_deref() == Some("codex") { " ●" } else { " ○" }.to_string(),
            FilterMenuItem::MinLines => match app.filter_min_lines {
                Some(n) => format!(" [≥{}]", n),
                None => " [Any]".to_string(),
            },
            FilterMenuItem::AfterDate => match &app.filter_after_date_display {
                Some(d) => format!(" [>{}]", d),
                None => " [None]".to_string(),
            },
            FilterMenuItem::BeforeDate => match &app.filter_before_date_display {
                Some(d) => format!(" [<{}]", d),
                None => " [None]".to_string(),
            },
        };

        let style = if is_selected {
            Style::default().bg(t.selection_bg).fg(t.selection_header_fg)
        } else {
            Style::default()
        };

        let prefix = if is_selected { "▶ " } else { "  " };
        lines.push(Line::from(vec![
            Span::styled(prefix, style),
            Span::styled(item.label(), style),
            Span::styled(state_indicator, Style::default().fg(t.match_fg)),
        ]));
    }

    let paragraph = Paragraph::new(lines);
    frame.render_widget(paragraph, inner);
}

fn render_scope_modal(frame: &mut Frame, app: &App, t: &Theme, area: Rect) {
    use ratatui::widgets::{Block, Borders, Clear};

    // Center the modal (wider to fit full directory paths)
    let modal_width = 80u16;
    let modal_height = 7u16; // 3 items + 2 border + 2 padding
    let x = (area.width.saturating_sub(modal_width)) / 2;
    let y = (area.height.saturating_sub(modal_height)) / 2;
    let modal_area = Rect::new(x, y, modal_width, modal_height);

    // Clear the area behind the modal
    frame.render_widget(Clear, modal_area);

    // Modal border
    let block = Block::default()
        .title(" Scope (/) ")
        .borders(Borders::ALL)
        .style(Style::default().bg(t.search_bg));
    frame.render_widget(block, modal_area);

    // Inner content area
    let inner = Rect::new(x + 2, y + 1, modal_width - 4, modal_height - 2);

    // Build menu items based on current state
    // Show full path if short, ~/.../<dir> if long (same logic as scope_display)
    let home = std::env::var("HOME").unwrap_or_default();
    let cwd_display = {
        let path = if !home.is_empty() && app.launch_cwd.starts_with(&home) {
            format!("~{}", &app.launch_cwd[home.len()..])
        } else {
            app.launch_cwd.clone()
        };
        if path.len() > 50 {
            let last = std::path::Path::new(&app.launch_cwd)
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("");
            format!("~/.../{}", last)
        } else {
            path
        }
    };
    // Build current directory label with optional branch
    let current_dir_label = if let Some(ref branch) = app.filter_branch {
        format!("Current directory ({}) [⎇ {}]", cwd_display, branch)
    } else {
        format!("Current directory ({})", cwd_display)
    };

    let items: Vec<(String, bool)> = vec![
        ("Global (everywhere)".to_string(), app.scope_global && app.filter_dir.is_none()),
        (current_dir_label, !app.scope_global && app.filter_dir.is_none()),
        ("Custom directory/branch...".to_string(), app.filter_dir.is_some()),
    ];

    let mut lines: Vec<Line> = Vec::new();

    for (i, (label, is_active)) in items.iter().enumerate() {
        let is_selected = i == app.scope_modal_selected;

        let style = if is_selected {
            Style::default().bg(t.selection_bg).fg(t.selection_header_fg)
        } else {
            Style::default()
        };

        let prefix = if is_selected { "▶ " } else { "  " };
        let state = if *is_active { " ●" } else { " ○" };

        // For custom directory, show the path and branch if set
        let suffix = if i == 2 {
            if let Some(ref dir) = app.filter_dir {
                let home = std::env::var("HOME").unwrap_or_default();
                let dir_display = if !home.is_empty() && dir.starts_with(&home) {
                    format!("~{}", &dir[home.len()..])
                } else {
                    dir.clone()
                };
                // Add branch if set
                let full_display = if let Some(ref branch) = app.filter_branch {
                    format!(" [{}:{}]", dir_display, branch)
                } else {
                    format!(" [{}]", dir_display)
                };
                // Truncate if too long
                if full_display.len() > 40 {
                    format!("{}...]", &full_display[..37])
                } else {
                    full_display
                }
            } else if let Some(ref branch) = app.filter_branch {
                // Only branch set, no custom dir
                format!(" [:{}]", branch)
            } else {
                String::new()
            }
        } else {
            String::new()
        };

        lines.push(Line::from(vec![
            Span::styled(prefix, style),
            Span::styled(label.clone(), style),
            Span::styled(state, Style::default().fg(t.match_fg)),
            Span::styled(suffix, Style::default().fg(t.dim_fg)),
        ]));
    }

    let paragraph = Paragraph::new(lines);
    frame.render_widget(paragraph, inner);
}

fn render_search_bar(frame: &mut Frame, app: &App, t: &Theme, area: Rect) {
    // Layout: [search...] [N sessions] / ~/path/to/dir
    // Give more space to directory path by making search box smaller
    let scope_label = app.scope_display();
    let session_count = format!("{} sessions", app.filtered.len());

    // Right side: " | N | / path "
    // Calculate widths: separator(3) + count + separator(3) + keycap(3) + scope + padding(2)
    let right_side_width = 3 + session_count.len() + 3 + 3 + scope_label.len() + 2;
    // Make search box smaller to give more space to directory path (shift right side left by ~20 chars)
    let search_width = (area.width as usize).saturating_sub(right_side_width + 32);

    let middle_line = if app.query.is_empty() {
        let placeholder = " Search...";
        let padding = search_width.saturating_sub(placeholder.len());
        Line::from(vec![
            Span::styled(placeholder, Style::default().fg(t.placeholder_fg)),
            Span::raw(" ".repeat(padding)),
            Span::styled(" │ ", Style::default().fg(t.separator_fg)),
            Span::styled(&session_count, Style::default().fg(t.dim_fg)),
            Span::styled(" │ ", Style::default().fg(t.separator_fg)),
            Span::styled(" / ", Style::default().bg(t.keycap_bg)),
            Span::styled(format!(" {}", scope_label), Style::default().fg(t.scope_label_fg)),
        ])
    } else {
        let query_len = 1 + app.query.chars().count() + 1;
        let padding = search_width.saturating_sub(query_len);
        Line::from(vec![
            Span::raw(" "),
            Span::raw(&app.query),
            Span::styled("█", Style::default().fg(t.accent)),
            Span::raw(" ".repeat(padding)),
            Span::styled(" │ ", Style::default().fg(t.separator_fg)),
            Span::styled(&session_count, Style::default().fg(t.dim_fg)),
            Span::styled(" │ ", Style::default().fg(t.separator_fg)),
            Span::styled(" / ", Style::default().bg(t.keycap_bg)),
            Span::styled(format!(" {}", scope_label), Style::default().fg(t.scope_label_fg)),
        ])
    };

    let separator_pos = search_width;
    let lines = vec![
        Line::from(vec![
            Span::raw(" ".repeat(separator_pos)),
            Span::styled(" │ ", Style::default().fg(t.separator_fg)),
        ]),
        middle_line,
        Line::from(vec![
            Span::raw(" ".repeat(separator_pos)),
            Span::styled(" │ ", Style::default().fg(t.separator_fg)),
        ]),
    ];

    let paragraph = Paragraph::new(lines).style(Style::default().bg(t.search_bg));
    frame.render_widget(paragraph, area);
}

fn render_session_list(frame: &mut Frame, app: &mut App, t: &Theme, area: Rect) {
    let available_width = area.width.saturating_sub(2) as usize;

    if app.filtered.is_empty() {
        let msg = if app.query.is_empty() {
            "No sessions"
        } else {
            "No results"
        };
        let paragraph = Paragraph::new(Span::styled(msg, Style::default().fg(t.dim_fg)));
        frame.render_widget(paragraph, area);
        return;
    }

    // Calculate field widths based on max values
    let row_num_width = app.filtered.len().to_string().len().max(2);
    let sep = " | ";

    // Calculate max widths for each field - no artificial caps, show full names
    let mut max_session_id_len = 0usize;
    let mut max_project_len = 0usize;
    let mut max_branch_len = 0usize;
    let mut max_lines_len = 0usize;
    for &idx in &app.filtered {
        let s = &app.sessions[idx];
        max_session_id_len = max_session_id_len.max(s.session_id_display().len());
        max_project_len = max_project_len.max(s.project_name().len());
        max_branch_len = max_branch_len.max(s.branch_display().len());
        max_lines_len = max_lines_len.max(format!("{}L", s.lines).len());
    }
    // Ensure minimums and reasonable maximums
    max_session_id_len = max_session_id_len.max(8).min(18);
    max_project_len = max_project_len.max(10).min(40);
    max_branch_len = max_branch_len.max(8).min(35);
    max_lines_len = max_lines_len.max(4);

    // Calculate available width and determine date format
    // Fixed overhead: row_num + space + icon/agent (8) + 4 separators (12) + padding (2)
    let fixed_overhead = row_num_width + 1 + 8 + 12 + 2;
    let available_width = area.width as usize;

    // Width needed for non-date fields
    let non_date_width = fixed_overhead + max_session_id_len + max_project_len + max_branch_len + max_lines_len;
    let remaining_for_date = available_width.saturating_sub(non_date_width);

    // Determine date format based on available space
    // Full: ~19 chars ("11/27 - 11/29 15:23"), Medium: ~13 chars ("11/27 - 11/29"), Compact: ~4 chars ("35d")
    let date_format = if remaining_for_date >= 19 {
        "full"
    } else if remaining_for_date >= 13 {
        "medium"
    } else {
        "compact"
    };

    // If even medium date doesn't fit well, also truncate branch more aggressively
    let effective_branch_len = if remaining_for_date < 13 && max_branch_len > 15 {
        15  // Truncate branch to 15 chars to make more room
    } else if remaining_for_date < 19 && max_branch_len > 20 {
        20  // Truncate branch to 20 chars
    } else {
        max_branch_len
    };

    // Calculate max date length based on format
    let max_date_len = match date_format {
        "full" => 19,
        "medium" => 13,
        _ => 4,
    };

    let items: Vec<ListItem> = app
        .filtered
        .iter()
        .enumerate()
        .map(|(i, &idx)| {
            let s = &app.sessions[idx];
            let is_selected = i == app.selected;
            let row_num = i + 1; // 1-indexed

            let source_color = if s.agent == "claude" {
                t.claude_source
            } else {
                t.codex_source
            };

            let header_style = if is_selected {
                Style::default().fg(t.selection_header_fg)
            } else {
                Style::default()
            };

            let sep_style = Style::default().fg(t.separator_fg);

            // Agent icon + abbreviation
            let (agent_icon, agent_abbrev) = if s.agent == "claude" {
                ("●", "CLD")
            } else {
                ("■", "CDX")
            };

            // Format: row# [icon Agent] session_id | project | branch | lines | date
            let row_num_str = format!("{:>width$}", row_num, width = row_num_width);
            let session_display = format!("{:<width$}", s.session_id_display(), width = max_session_id_len);
            let project_padded = format!("{:<width$}", truncate(s.project_name(), max_project_len), width = max_project_len);
            let branch_padded = format!("{:<width$}", truncate(s.branch_display(), effective_branch_len), width = effective_branch_len);
            let lines_str = format!("{:>width$}", format!("{}L", s.lines), width = max_lines_len);

            // Choose date format based on available space
            let date_text = match date_format {
                "full" => s.date_display(),
                "medium" => s.date_medium(),
                _ => s.date_compact(),
            };
            let date_str = format!("{:>width$}", date_text, width = max_date_len);

            let header_spans = vec![
                Span::styled(format!("{} ", row_num_str), Style::default().fg(t.dim_fg)),
                Span::styled(format!("{} {} ", agent_icon, agent_abbrev), Style::default().fg(source_color)),
                Span::styled(session_display, Style::default().fg(t.dim_fg)),
                Span::styled(sep, sep_style),
                Span::styled(project_padded, header_style),
                Span::styled(sep, sep_style),
                Span::styled(branch_padded, Style::default().fg(t.accent)),
                Span::styled(sep, sep_style),
                Span::styled(lines_str, header_style),
                Span::styled(sep, sep_style),
                Span::styled(date_str, Style::default().fg(t.dim_fg)),
            ];

            // Snippet: show last_msg when no query, highlighted match when searching
            let snippet_style = if is_selected {
                Style::default().fg(t.selection_snippet_fg)
            } else {
                Style::default().fg(t.snippet_fg)
            };
            let highlight_style = Style::default().fg(t.match_fg);

            // Indent snippet to align with content (after row number)
            let indent = " ".repeat(row_num_width + 1);
            let snippet_width = available_width.saturating_sub(row_num_width + 1);

            // Build custom title prefix if present
            let title_style = Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD);
            let (title_prefix, title_len) = if !s.custom_title.is_empty() {
                let prefix = format!("[{}] ", s.custom_title);
                let len = prefix.len();
                (Some(prefix), len)
            } else {
                (None, 0)
            };
            let effective_snippet_width = snippet_width.saturating_sub(title_len);

            let snippet_line = if app.query.is_empty() {
                // No query: show last message content
                let snippet = truncate(&s.last_msg_content, effective_snippet_width);
                let mut spans = vec![Span::styled(indent.clone(), snippet_style)];
                if let Some(ref tp) = title_prefix {
                    spans.push(Span::styled(tp.clone(), title_style));
                }
                spans.push(Span::styled(format!("...{}", snippet), snippet_style));
                Line::from(spans)
            } else {
                // With query: use Tantivy snippet with HTML tags for highlighting
                if let Some(snippet_html) = app.search_snippets.get(&s.session_id) {
                    // Truncate the plain text version but render with HTML tags
                    let snippet_plain = strip_html_tags(snippet_html);
                    let truncated_plain = truncate(&snippet_plain, effective_snippet_width);
                    // Find how much of the HTML snippet to use based on plain text length
                    let mut spans = vec![Span::styled(indent.clone(), snippet_style)];
                    if let Some(ref tp) = title_prefix {
                        spans.push(Span::styled(tp.clone(), title_style));
                    }
                    // Truncate HTML snippet approximately (allow extra for tags)
                    let html_truncated: String = snippet_html.chars().take(effective_snippet_width + 50).collect();
                    spans.extend(render_snippet_with_html_tags(&html_truncated, snippet_style, highlight_style));
                    Line::from(spans)
                } else {
                    let snippet = truncate(&s.first_msg_content, effective_snippet_width);
                    let mut spans = vec![Span::styled(indent.clone(), snippet_style)];
                    if let Some(ref tp) = title_prefix {
                        spans.push(Span::styled(tp.clone(), title_style));
                    }
                    spans.push(Span::styled(format!("...{}", snippet), snippet_style));
                    Line::from(spans)
                }
            };

            let lines = vec![
                Line::from(header_spans),
                snippet_line,
                Line::from(""),
            ];

            if is_selected {
                ListItem::new(lines).style(Style::default().bg(t.selection_bg))
            } else {
                ListItem::new(lines)
            }
        })
        .collect();

    let list = List::new(items);

    // Calculate visible items (3 lines per item)
    let lines_per_item = 3;
    let visible_items = (area.height as usize) / lines_per_item;

    if app.selected < app.list_scroll {
        app.list_scroll = app.selected;
    } else if app.selected >= app.list_scroll + visible_items && visible_items > 0 {
        app.list_scroll = app.selected - visible_items + 1;
    }

    let mut list_state = ListState::default();
    list_state.select(Some(app.selected));
    *list_state.offset_mut() = app.list_scroll;

    frame.render_stateful_widget(list, area, &mut list_state);
}

fn render_preview(frame: &mut Frame, app: &mut App, t: &Theme, area: Rect) {
    let Some(s) = app.selected_session() else {
        return;
    };

    let bubble_width = area.width.saturating_sub(4) as usize;
    let mut lines: Vec<Line> = Vec::new();

    // First message - labeled as "FIRST MESSAGE"
    if !s.first_msg_content.is_empty() {
        let (role_label, label_color, bubble_bg) = if s.first_msg_role == "user" {
            ("User", t.user_label, t.user_bubble_bg)
        } else if s.agent == "claude" {
            ("Claude", t.claude_source, t.claude_bubble_bg)
        } else {
            ("Codex", t.codex_source, t.codex_bubble_bg)
        };

        lines.push(Line::from(vec![
            Span::styled(" ── FIRST ── ", Style::default().fg(t.dim_fg)),
            Span::styled(role_label, Style::default().fg(label_color).add_modifier(Modifier::BOLD)),
        ]));

        for wrapped in wrap_text(&s.first_msg_content, bubble_width).iter().take(6) {
            let padding = bubble_width.saturating_sub(wrapped.chars().count());
            lines.push(Line::from(vec![
                Span::styled(" ", Style::default().bg(bubble_bg)),
                Span::styled(wrapped.clone(), Style::default().bg(bubble_bg)),
                Span::styled(" ".repeat(padding + 1), Style::default().bg(bubble_bg)),
            ]));
        }

        lines.push(Line::from(""));
    }

    // Search snippet - show matching content when searching (with keyword highlighting)
    if !app.query.is_empty() {
        if let Some(snippet) = app.search_snippets.get(&s.session_id) {
            if !snippet.is_empty() {
                lines.push(Line::from(vec![
                    Span::styled(" ── MATCH ── ", Style::default().fg(t.accent).add_modifier(Modifier::BOLD)),
                ]));

                // Styles for the match snippet
                let match_bg = Color::Rgb(50, 40, 30); // Warm/highlighted background
                let base_style = Style::default().bg(match_bg).fg(t.accent);
                let highlight_style = Style::default().bg(Color::Yellow).fg(Color::Black).add_modifier(Modifier::BOLD);

                // Strip HTML tags for wrapping calculation, but use original for display
                let snippet_plain = strip_html_tags(snippet);
                // Display 12 lines (50% more than original 8)
                for wrapped in wrap_text(snippet, bubble_width + 7).iter().take(12) {
                    // Account for <b></b> tags in padding calculation
                    let visible_chars = strip_html_tags(wrapped).chars().count();
                    let padding = bubble_width.saturating_sub(visible_chars);

                    // Build line with HTML tag-based highlighting
                    let mut line_spans: Vec<Span> = Vec::new();
                    line_spans.push(Span::styled(" ", Style::default().bg(match_bg)));

                    // Parse <b>...</b> tags for highlighting
                    let highlighted = render_snippet_with_html_tags(wrapped, base_style, highlight_style);
                    line_spans.extend(highlighted);

                    line_spans.push(Span::styled(" ".repeat(padding + 1), Style::default().bg(match_bg)));
                    lines.push(Line::from(line_spans));
                }

                lines.push(Line::from(""));
            }
        }
    }

    // Last message - labeled as "LAST MESSAGE" (if different from first)
    if !s.last_msg_content.is_empty() && s.last_msg_content != s.first_msg_content {
        let (role_label, label_color, bubble_bg) = if s.last_msg_role == "user" {
            ("User", t.user_label, t.user_bubble_bg)
        } else if s.agent == "claude" {
            ("Claude", t.claude_source, t.claude_bubble_bg)
        } else {
            ("Codex", t.codex_source, t.codex_bubble_bg)
        };

        lines.push(Line::from(vec![
            Span::styled(" ── LAST ── ", Style::default().fg(t.dim_fg)),
            Span::styled(role_label, Style::default().fg(label_color).add_modifier(Modifier::BOLD)),
        ]));

        for wrapped in wrap_text(&s.last_msg_content, bubble_width).iter().take(6) {
            let padding = bubble_width.saturating_sub(wrapped.chars().count());
            lines.push(Line::from(vec![
                Span::styled(" ", Style::default().bg(bubble_bg)),
                Span::styled(wrapped.clone(), Style::default().bg(bubble_bg)),
                Span::styled(" ".repeat(padding + 1), Style::default().bg(bubble_bg)),
            ]));
        }
    }

    // Clamp scroll
    let visible_height = area.height as usize;
    let max_scroll = lines.len().saturating_sub(visible_height.min(lines.len()));
    app.preview_scroll = app.preview_scroll.min(max_scroll);

    let visible_lines: Vec<Line> = lines.into_iter().skip(app.preview_scroll).collect();
    let paragraph = Paragraph::new(visible_lines);
    frame.render_widget(paragraph, area);
}

fn render_status_bar(frame: &mut Frame, app: &App, t: &Theme, area: Rect, show_legend: bool) {
    // Show status message if present (e.g., "Copied to clipboard")
    if let Some(ref msg) = app.status_message {
        let status_line = Line::from(vec![
            Span::styled(format!(" ✓ {} ", msg), Style::default().fg(Color::Green)),
            Span::styled(" (press any key to dismiss)", Style::default().fg(t.dim_fg)),
        ]);
        frame.render_widget(Paragraph::new(status_line), area);
        return;
    }

    // Check if we have any active filters (need third row for legend or filters)
    let has_filters = !app.include_original
        || app.include_sub
        || !app.include_trimmed
        || !app.include_continued
        || app.filter_agent.is_some()
        || app.filter_min_lines.is_some()
        || app.filter_after_date.is_some()
        || app.filter_before_date.is_some()
        || (!app.scope_global && app.filter_branch.is_some());

    let needs_legend_row = show_legend || has_filters;

    // Split area: line 1 (shortcuts), optional line 2 (legend + filters)
    let status_layout = if needs_legend_row {
        Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Length(1), Constraint::Length(1)])
            .split(area)
    } else {
        Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Length(1)])
            .split(area)
    };

    let nav_area = status_layout[0];

    let keycap = Style::default().bg(t.keycap_bg);
    let label = Style::default();
    let dim = Style::default().fg(t.dim_fg);
    let filter_active = Style::default().fg(t.match_fg);

    // Line 1: Navigation shortcuts OR input mode indicator
    let mut nav_spans: Vec<Span> = Vec::new();

    if let Some(ref mode) = app.input_mode {
        // Input mode indicator
        let prompt = match mode {
            InputMode::MinLines => format!(" Min lines: {}█ ", app.input_buffer),
            InputMode::Agent => " Agent: 1=Claude 2=Codex 0=All ".to_string(),
            InputMode::JumpToLine => format!(" Go to row: {}█ ", app.input_buffer),
            InputMode::AfterDate => format!(" After date: {}█ (any format) ", app.input_buffer),
            InputMode::BeforeDate => format!(" Before date: {}█ (any format) ", app.input_buffer),
            InputMode::ScopeDir => format!(" Scope: {}█ (dir:branch | :branch | empty=global) ", app.input_buffer),
            InputMode::Branch => format!(" Branch: {}█ (Enter=apply, empty=clear) ", app.input_buffer),
        };
        nav_spans.push(Span::styled(prompt, Style::default().bg(t.accent).fg(Color::Black)));
    } else if app.command_mode {
        // Command mode indicator
        nav_spans.push(Span::styled(" CMD ", Style::default().bg(t.accent).fg(Color::Black)));
        nav_spans.push(Span::styled(" :x clear :o orig :s sub :t trim :c cont :a agent :m lines :> after :< before ", label));
    } else {
        // Normal mode - single line with all shortcuts
        let has_selection = !app.filtered.is_empty();

        // Navigation group: [↑/↓ PgUp/Dn Home/End] Nav
        nav_spans.extend([
            Span::styled(" ↑/↓ PgUp/Dn Home/End ", keycap),
            Span::styled(" nav ", label),
        ]);

        if has_selection {
            nav_spans.extend([
                Span::styled("│ ", dim),
                Span::styled(" Enter ", keycap),
                Span::styled(" actions ", label),
            ]);
        }

        nav_spans.extend([
            Span::styled("│ ", dim),
            Span::styled(" / ", keycap),
            Span::styled(" dir[:branch] ", label),
        ]);

        nav_spans.extend([
            Span::styled("│ ", dim),
            Span::styled(" C-f ", keycap),
            Span::styled(" filter ", label),
            Span::styled("│ ", dim),
            Span::styled(" C-s ", keycap),
            Span::styled(if app.sort_by_time { " match-sort " } else { " time-sort " }, label),
            Span::styled("│ ", dim),
            Span::styled(" Esc ", keycap),
            Span::styled(" quit", label),
        ]);
    }

    let nav_line = Line::from(nav_spans);
    frame.render_widget(Paragraph::new(nav_line), nav_area);

    // Second row: annotation legend (if needed) + active filter indicators
    if needs_legend_row {
        let mut row3_spans: Vec<Span> = Vec::new();

        // Annotation legend (if annotations exist in results)
        // Note: "rolled-over" sessions are internally called "continued"
        if show_legend {
            row3_spans.extend([
                Span::styled("  ", dim),
                Span::styled("(r)", Style::default().fg(t.dim_fg)),
                Span::styled(" rolled-over  ", dim),
                Span::styled("(t)", Style::default().fg(t.dim_fg)),
                Span::styled(" trimmed  ", dim),
                Span::styled("(s)", Style::default().fg(t.dim_fg)),
                Span::styled(" sub-agent", dim),
            ]);
        }

        // Active filters
        if !app.include_original {
            row3_spans.push(Span::styled(" [-orig]", filter_active));
        }
        if app.include_sub {
            row3_spans.push(Span::styled(" [+sub]", filter_active));
        }
        if !app.include_trimmed {
            row3_spans.push(Span::styled(" [-trim]", filter_active));
        }
        if !app.include_continued {
            row3_spans.push(Span::styled(" [-roll]", filter_active));
        }
        if let Some(ref agent) = app.filter_agent {
            row3_spans.push(Span::styled(format!(" [{}]", agent), filter_active));
        }
        if let Some(min) = app.filter_min_lines {
            row3_spans.push(Span::styled(format!(" [≥{}L]", min), filter_active));
        }
        if let Some(ref date) = app.filter_after_date_display {
            row3_spans.push(Span::styled(format!(" [>{}]", date), filter_active));
        }
        if let Some(ref date) = app.filter_before_date_display {
            row3_spans.push(Span::styled(format!(" [<{}]", date), filter_active));
        }
        // Branch filter - only show when not in global scope
        if !app.scope_global {
            if let Some(ref branch) = app.filter_branch {
                row3_spans.push(Span::styled(format!(" [⎇ {}]", branch), filter_active));
            }
        }

        let legend_row = Paragraph::new(Line::from(row3_spans));
        frame.render_widget(legend_row, status_layout[1]);
    }
}

fn render_full_conversation(frame: &mut Frame, app: &mut App, t: &Theme) {
    let area = frame.area();

    // Layout: header (2 lines), content, footer (1 line)
    let layout = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(2), // Header
            Constraint::Min(0),    // Content
            Constraint::Length(1), // Footer
        ])
        .split(area);

    // Header - session info
    if let Some(s) = app.selected_session() {
        let source_color = if s.agent == "claude" {
            t.claude_source
        } else {
            t.codex_source
        };

        let header = Line::from(vec![
            Span::styled(
                format!(" {} {} ", s.agent_icon(), s.agent_display()),
                Style::default().fg(source_color).add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!("{}  ", s.session_id_display()),
                Style::default().fg(t.dim_fg),
            ),
            Span::styled(
                format!("{}  ", s.project_name()),
                Style::default().add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!("{}  ", s.branch_display()),
                Style::default().fg(t.accent),
            ),
            Span::styled(
                format!("{}L", s.lines),
                Style::default().fg(t.dim_fg),
            ),
        ]);
        frame.render_widget(Paragraph::new(header), layout[0]);
    }

    // Determine agent label (with icon) and colors for assistant messages
    let (agent_label, assistant_bg, assistant_fg) = if let Some(s) = app.selected_session() {
        if s.agent == "claude" {
            ("● Claude", t.claude_bubble_bg, t.claude_source)
        } else {
            ("■ Codex", t.codex_bubble_bg, t.codex_source)
        }
    } else {
        ("● Assistant", t.claude_bubble_bg, t.claude_source)
    };

    let content_width = layout[1].width.saturating_sub(2) as usize;

    // Original query highlighting (blue) - pre-process with SnippetGenerator
    let query_highlight = Style::default().bg(Color::Rgb(30, 80, 180)).fg(Color::White).add_modifier(Modifier::BOLD);
    let mut query_html_lines: Vec<String> = Vec::new();
    if !app.query.is_empty() {
        if let Ok(index) = Index::open_in_dir(&app.index_path) {
            if let Ok(content_field) = index.schema().get_field("content") {
                let query_parser = QueryParser::for_index(&index, vec![content_field]);
                let parsed_query = query_parser.parse_query_lenient(&app.query).0;
                if let Ok(reader) = index.reader() {
                    let searcher = reader.searcher();
                    if let Ok(mut gen) = SnippetGenerator::create(&searcher, &*parsed_query, content_field) {
                        gen.set_max_num_chars(10000); // Large enough for full lines
                        for line in app.full_content.lines() {
                            let html = gen.snippet(line).to_html();
                            let merged = merge_adjacent_highlights(&html);
                            query_html_lines.push(if merged.is_empty() { line.to_string() } else { merged });
                        }
                    }
                }
            }
        }
    }
    let use_query_html = !query_html_lines.is_empty() && query_html_lines.len() == app.full_content.lines().count();

    // View search highlighting (yellow) - from / command
    let search_pattern = &app.view_search_pattern;
    let search_highlight = Style::default().bg(Color::Yellow).fg(Color::Black);

    // Content - full conversation with styled messages
    // Track current message context for continuation lines
    #[derive(Clone, Copy, PartialEq)]
    enum MsgContext { None, User, Assistant }
    let mut context = MsgContext::None;

    // Helper to get HTML version of content (skipping prefix chars)
    let get_html_content = |idx: usize, skip_chars: usize, original: &str| -> String {
        if use_query_html {
            query_html_lines[idx].chars().skip(skip_chars).collect()
        } else {
            original.chars().skip(skip_chars).collect()
        }
    };

    let content_lines: Vec<Line> = app
        .full_content
        .lines()
        .enumerate()
        .map(|(idx, line)| {
            if line.starts_with("> ") {
                // User message - skip "> " (2 chars)
                context = MsgContext::User;
                let msg_content: String = line.chars().skip(2).collect();
                let html_content = get_html_content(idx, 2, line);
                let used = 6 + 1 + msg_content.chars().count(); // " User " + " " + content
                let padding = content_width.saturating_sub(used);
                let base_style = Style::default().bg(t.user_bubble_bg);
                let mut spans = vec![
                    Span::styled(" User ", Style::default().fg(t.user_label).add_modifier(Modifier::BOLD)),
                    Span::styled(" ", base_style),
                ];
                spans.extend(render_with_dual_highlighting(&html_content, search_pattern, base_style, query_highlight, search_highlight));
                spans.push(Span::styled(" ".repeat(padding), base_style));
                Line::from(spans)
            } else if line.starts_with("⏺ ") {
                // Assistant message - ⏺ is 3 bytes + space = 4 bytes
                context = MsgContext::Assistant;
                let msg_content: String = line.chars().skip(2).collect(); // Skip icon + space
                let html_content = get_html_content(idx, 2, line);
                let label_with_space = format!(" {} ", agent_label);
                let used = label_with_space.chars().count() + 1 + msg_content.chars().count();
                let padding = content_width.saturating_sub(used);
                let base_style = Style::default().bg(assistant_bg);
                let mut spans = vec![
                    Span::styled(label_with_space, Style::default().fg(assistant_fg).add_modifier(Modifier::BOLD)),
                    Span::styled(" ", base_style),
                ];
                spans.extend(render_with_dual_highlighting(&html_content, search_pattern, base_style, query_highlight, search_highlight));
                spans.push(Span::styled(" ".repeat(padding), base_style));
                Line::from(spans)
            } else if line.starts_with("  ⎿") {
                // Tool result - style as dimmed (2 spaces + ⎿ character)
                context = MsgContext::None;
                let content: String = line.chars().skip(3).collect(); // Skip "  ⎿"
                let html_content = get_html_content(idx, 3, line);
                let base_style = Style::default().fg(t.dim_fg);
                let mut spans = vec![Span::styled("      ", base_style)];
                spans.extend(render_with_dual_highlighting(&html_content, search_pattern, base_style, query_highlight, search_highlight));
                Line::from(spans)
            } else if line.is_empty() {
                // Empty line - keep context for multi-paragraph messages
                Line::from("")
            } else if context != MsgContext::None {
                // Continuation line within a message block (indented or not)
                let html_line = if use_query_html { &query_html_lines[idx] } else { line };
                match context {
                    MsgContext::User => {
                        let used = 6 + 1 + line.chars().count(); // prefix + " " + content
                        let padding = content_width.saturating_sub(used);
                        let base_style = Style::default().bg(t.user_bubble_bg);
                        let mut spans = vec![
                            Span::styled("      ", Style::default()),
                            Span::styled(" ", base_style),
                        ];
                        spans.extend(render_with_dual_highlighting(html_line, search_pattern, base_style, query_highlight, search_highlight));
                        spans.push(Span::styled(" ".repeat(padding), base_style));
                        Line::from(spans)
                    }
                    MsgContext::Assistant => {
                        let label_width = agent_label.chars().count() + 2; // " ● Claude " chars
                        let used = label_width + 1 + line.chars().count();
                        let padding = content_width.saturating_sub(used);
                        let base_style = Style::default().bg(assistant_bg);
                        let mut spans = vec![
                            Span::styled(" ".repeat(label_width), Style::default()),
                            Span::styled(" ", base_style),
                        ];
                        spans.extend(render_with_dual_highlighting(html_line, search_pattern, base_style, query_highlight, search_highlight));
                        spans.push(Span::styled(" ".repeat(padding), base_style));
                        Line::from(spans)
                    }
                    MsgContext::None => {
                        let base_style = Style::default();
                        Line::from(render_with_dual_highlighting(html_line, search_pattern, base_style, query_highlight, search_highlight))
                    }
                }
            } else {
                // Plain line outside message context (metadata, etc.)
                let html_line = if use_query_html { &query_html_lines[idx] } else { line };
                let base_style = Style::default();
                Line::from(render_with_dual_highlighting(html_line, search_pattern, base_style, query_highlight, search_highlight))
            }
        })
        .collect();

    // Track total lines for footer display
    let total_lines = app.full_content.lines().count();

    // Clamp scroll to valid range
    let max_scroll = content_lines.len().saturating_sub(1);
    if app.full_content_scroll > max_scroll {
        app.full_content_scroll = max_scroll;
    }

    // Manually skip lines to scroll (so scroll works on content lines, not visual lines)
    // This ensures search navigation jumps to the correct content line
    let visible_lines: Vec<Line> = content_lines
        .into_iter()
        .skip(app.full_content_scroll)
        .collect();

    let content = Paragraph::new(visible_lines)
        .wrap(ratatui::widgets::Wrap { trim: false });
    frame.render_widget(content, layout[1]);

    // Footer - navigation hints or search input
    let keycap = Style::default().bg(t.keycap_bg);
    let label = Style::default();
    let dim = Style::default().fg(t.dim_fg);
    let highlight = Style::default().fg(t.match_fg);

    let footer = if app.view_search_mode {
        // Search input mode
        Line::from(vec![
            Span::styled(" /", Style::default().fg(t.accent)),
            Span::styled(&app.view_search_pattern, label),
            Span::styled("█", Style::default().fg(t.accent)),
            Span::styled("  [Enter: search original; keywords+Enter: search; Esc: cancel]", dim),
        ])
    } else if !app.view_search_pattern.is_empty() || app.query_nav_mode {
        // Active search mode - either view search (yellow) or query nav (blue/original)
        let (pattern_display, match_info) = if !app.view_search_pattern.is_empty() {
            // Yellow search mode
            let info = if app.view_search_matches.is_empty() {
                "No matches".to_string()
            } else {
                format!(
                    "Match {}/{}",
                    app.view_search_current + 1,
                    app.view_search_matches.len()
                )
            };
            (app.view_search_pattern.clone(), info)
        } else {
            // Blue/original query nav mode
            let info = if app.query_match_lines.is_empty() {
                "No matches".to_string()
            } else {
                format!(
                    "Match {}/{}",
                    app.query_match_current + 1,
                    app.query_match_lines.len()
                )
            };
            ("[original]".to_string(), info)
        };
        Line::from(vec![
            Span::styled(" /", Style::default().fg(t.accent)),
            Span::styled(pattern_display, highlight),
            Span::styled(format!("  {} ", match_info), dim),
            Span::styled(" │ ", dim),
            Span::styled(" f ", keycap),
            Span::styled(" next ", label),
            Span::styled(" d ", keycap),
            Span::styled(" prev ", label),
            Span::styled(" │ ", dim),
            Span::styled(" Esc ", keycap),
            Span::styled(" clear ", label),
            Span::styled(
                format!("  Line {}/{}", app.full_content_scroll + 1, total_lines),
                dim,
            ),
        ])
    } else {
        // Normal mode - show navigation hints
        Line::from(vec![
            Span::styled(" ↑↓/jk ", keycap),
            Span::styled(" scroll ", label),
            Span::styled(" │ ", dim),
            Span::styled(" PgUp/Dn ", keycap),
            Span::styled(" page ", label),
            Span::styled(" │ ", dim),
            Span::styled(" / ", keycap),
            Span::styled(" search ", label),
            Span::styled(" │ ", dim),
            Span::styled(" Home/End ", keycap),
            Span::styled(" jump ", label),
            Span::styled(" │ ", dim),
            Span::styled(" Space/Esc/q ", keycap),
            Span::styled(" back", label),
            Span::styled(
                format!("  Line {}/{}", app.full_content_scroll + 1, total_lines),
                dim,
            ),
        ])
    };
    frame.render_widget(Paragraph::new(footer), layout[2]);
}

// ============================================================================
// Helpers
// ============================================================================

fn truncate(s: &str, max: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    if chars.len() > max {
        format!("{}…", chars[..max - 1].iter().collect::<String>())
    } else {
        s.to_string()
    }
}

/// Find text containing query keywords and return spans with highlighted matches.
/// If query is empty, returns None. Otherwise returns Some(Vec<Span>) with highlighted keywords.
fn find_matching_snippet<'a>(
    content: &str,
    query: &str,
    max_len: usize,
    normal_style: Style,
    highlight_style: Style,
) -> Option<Vec<Span<'a>>> {
    if query.is_empty() {
        return None;
    }

    // Strip quotes from query for keyword extraction (phrase search still works via Tantivy)
    let query_clean = query.trim_matches('"').trim_matches('\'');
    let query_lower = query_clean.to_lowercase();
    let keywords: Vec<&str> = query_lower.split_whitespace().collect();
    if keywords.is_empty() {
        return None;
    }

    let content_lower = content.to_lowercase();

    // Find first occurrence of any keyword
    let mut best_pos = None;
    for keyword in &keywords {
        if let Some(pos) = content_lower.find(keyword) {
            match best_pos {
                None => best_pos = Some(pos),
                Some(current) if pos < current => best_pos = Some(pos),
                _ => {}
            }
        }
    }

    let start_pos = best_pos.unwrap_or(0);

    // Extract snippet around the match
    let half_len = max_len / 2;
    let snippet_start = start_pos.saturating_sub(half_len);
    let chars: Vec<char> = content.chars().collect();
    let snippet_end = (snippet_start + max_len).min(chars.len());

    let snippet: String = chars[snippet_start..snippet_end].iter().collect();
    let snippet_lower = snippet.to_lowercase();

    // Build spans with highlighted keywords
    let mut spans: Vec<Span> = Vec::new();
    let mut current_pos = 0;
    let snippet_chars: Vec<char> = snippet.chars().collect();
    let snippet_lower_chars: Vec<char> = snippet_lower.chars().collect();

    // Find all keyword positions in the snippet
    let mut highlights: Vec<(usize, usize)> = Vec::new();
    for keyword in &keywords {
        let kw_chars: Vec<char> = keyword.chars().collect();
        let mut search_pos = 0;
        while search_pos + kw_chars.len() <= snippet_lower_chars.len() {
            let match_found = (0..kw_chars.len())
                .all(|i| snippet_lower_chars[search_pos + i] == kw_chars[i]);
            if match_found {
                highlights.push((search_pos, search_pos + kw_chars.len()));
                search_pos += kw_chars.len();
            } else {
                search_pos += 1;
            }
        }
    }

    // Sort and merge overlapping highlights
    highlights.sort_by_key(|h| h.0);
    let mut merged: Vec<(usize, usize)> = Vec::new();
    for (start, end) in highlights {
        if let Some(last) = merged.last_mut() {
            if start <= last.1 {
                last.1 = last.1.max(end);
                continue;
            }
        }
        merged.push((start, end));
    }

    // Build spans
    if snippet_start > 0 {
        spans.push(Span::styled("...", normal_style));
    }

    for (start, end) in merged {
        // Add normal text before highlight
        if current_pos < start {
            let normal_text: String = snippet_chars[current_pos..start].iter().collect();
            spans.push(Span::styled(normal_text, normal_style));
        }
        // Add highlighted text
        let highlight_text: String = snippet_chars[start..end].iter().collect();
        spans.push(Span::styled(highlight_text, highlight_style));
        current_pos = end;
    }

    // Add remaining normal text
    if current_pos < snippet_chars.len() {
        let remaining: String = snippet_chars[current_pos..].iter().collect();
        spans.push(Span::styled(remaining, normal_style));
    }

    if snippet_end < chars.len() {
        spans.push(Span::styled("...", normal_style));
    }

    Some(spans)
}

/// Highlight multiple keywords in text (from space-separated query), returning styled spans.
fn highlight_keywords_in_line<'a>(
    text: &str,
    query: &str,
    base_style: Style,
    highlight_style: Style,
) -> Vec<Span<'a>> {
    if query.is_empty() || text.is_empty() {
        return vec![Span::styled(text.to_string(), base_style)];
    }

    // Strip quotes from query for keyword extraction
    let query_clean = query.trim_matches('"').trim_matches('\'');
    let query_lower = query_clean.to_lowercase();
    let keywords: Vec<&str> = query_lower.split_whitespace().collect();
    if keywords.is_empty() {
        return vec![Span::styled(text.to_string(), base_style)];
    }

    let text_lower = text.to_lowercase();
    let text_chars: Vec<char> = text.chars().collect();
    let text_lower_chars: Vec<char> = text_lower.chars().collect();

    // Find all keyword positions
    let mut highlights: Vec<(usize, usize)> = Vec::new();
    for keyword in &keywords {
        let kw_chars: Vec<char> = keyword.chars().collect();
        if kw_chars.is_empty() {
            continue;
        }
        let mut search_pos = 0;
        while search_pos + kw_chars.len() <= text_lower_chars.len() {
            let match_found = (0..kw_chars.len())
                .all(|i| text_lower_chars[search_pos + i] == kw_chars[i]);
            if match_found {
                highlights.push((search_pos, search_pos + kw_chars.len()));
                search_pos += kw_chars.len();
            } else {
                search_pos += 1;
            }
        }
    }

    // No highlights found
    if highlights.is_empty() {
        return vec![Span::styled(text.to_string(), base_style)];
    }

    // Sort and merge overlapping highlights
    highlights.sort_by_key(|h| h.0);
    let mut merged: Vec<(usize, usize)> = Vec::new();
    for (start, end) in highlights {
        if let Some(last) = merged.last_mut() {
            if start <= last.1 {
                last.1 = last.1.max(end);
                continue;
            }
        }
        merged.push((start, end));
    }

    // Build spans
    let mut spans: Vec<Span> = Vec::new();
    let mut current_pos = 0;

    for (start, end) in merged {
        if current_pos < start {
            let normal_text: String = text_chars[current_pos..start].iter().collect();
            spans.push(Span::styled(normal_text, base_style));
        }
        let highlight_text: String = text_chars[start..end].iter().collect();
        spans.push(Span::styled(highlight_text, highlight_style));
        current_pos = end;
    }

    if current_pos < text_chars.len() {
        let remaining: String = text_chars[current_pos..].iter().collect();
        spans.push(Span::styled(remaining, base_style));
    }

    spans
}

/// Render snippet with Tantivy's <b> tags as highlighted spans.
/// Parses <b>...</b> tags and applies highlight_style to matched text.
fn render_snippet_with_html_tags<'a>(
    text: &str,
    base_style: Style,
    highlight_style: Style,
) -> Vec<Span<'a>> {
    let mut spans: Vec<Span<'a>> = Vec::new();
    let mut current_pos = 0;
    let bytes = text.as_bytes();

    while current_pos < text.len() {
        // Find next <b> tag
        if let Some(start_tag_pos) = text[current_pos..].find("<b>") {
            let abs_start = current_pos + start_tag_pos;

            // Add text before <b> as normal
            if abs_start > current_pos {
                spans.push(Span::styled(text[current_pos..abs_start].to_string(), base_style));
            }

            // Find closing </b>
            let content_start = abs_start + 3; // skip "<b>"
            if let Some(end_tag_pos) = text[content_start..].find("</b>") {
                let content_end = content_start + end_tag_pos;
                // Add highlighted text
                spans.push(Span::styled(text[content_start..content_end].to_string(), highlight_style));
                current_pos = content_end + 4; // skip "</b>"
            } else {
                // No closing tag, treat rest as normal
                spans.push(Span::styled(text[current_pos..].to_string(), base_style));
                break;
            }
        } else {
            // No more <b> tags, add remaining text as normal
            spans.push(Span::styled(text[current_pos..].to_string(), base_style));
            break;
        }
    }

    if spans.is_empty() {
        spans.push(Span::styled(text.to_string(), base_style));
    }

    spans
}

/// Strip HTML tags from snippet for plain text output (e.g., JSON)
fn strip_html_tags(text: &str) -> String {
    text.replace("<b>", "").replace("</b>", "")
}

/// Merge adjacent <b> tags to highlight phrases as a unit.
/// Converts "<b>single</b> <b>fix</b>" to "<b>single fix</b>"
/// This improves phrase query highlighting since Tantivy highlights terms individually.
fn merge_adjacent_highlights(html: &str) -> String {
    // Pattern: </b> followed by whitespace and then <b>
    // We want to replace "</b> <b>" (and variants with multiple spaces) with just a space
    let mut result = html.to_string();
    // Handle single space
    result = result.replace("</b> <b>", " ");
    // Handle multiple spaces (normalize to single)
    result = result.replace("</b>  <b>", " ");
    result = result.replace("</b>   <b>", " ");
    // Handle newlines between terms
    result = result.replace("</b>\n<b>", " ");
    result
}

/// Render text with two-layer highlighting:
/// 1. Blue highlighting from HTML <b> tags (original query via SnippetGenerator)
/// 2. Yellow highlighting for view search pattern (overlays on top)
fn render_with_dual_highlighting<'a>(
    html_text: &str,          // Text with <b> tags from SnippetGenerator
    view_pattern: &str,       // View search pattern (may be empty)
    base_style: Style,
    query_highlight: Style,   // Blue for original query
    view_highlight: Style,    // Yellow for view search
) -> Vec<Span<'a>> {
    // First pass: parse <b> tags and build a list of (text, is_query_match)
    let mut segments: Vec<(String, bool)> = Vec::new();
    let mut current_pos = 0;

    while current_pos < html_text.len() {
        if let Some(start_tag_pos) = html_text[current_pos..].find("<b>") {
            let abs_start = current_pos + start_tag_pos;
            // Add text before <b> as non-match
            if abs_start > current_pos {
                segments.push((html_text[current_pos..abs_start].to_string(), false));
            }
            // Find closing </b>
            let content_start = abs_start + 3;
            if let Some(end_tag_pos) = html_text[content_start..].find("</b>") {
                let content_end = content_start + end_tag_pos;
                // Add matched text
                segments.push((html_text[content_start..content_end].to_string(), true));
                current_pos = content_end + 4;
            } else {
                segments.push((html_text[current_pos..].to_string(), false));
                break;
            }
        } else {
            segments.push((html_text[current_pos..].to_string(), false));
            break;
        }
    }

    if segments.is_empty() {
        return vec![Span::styled(html_text.to_string(), base_style)];
    }

    // Second pass: for each segment, apply view search highlighting on top
    let mut spans: Vec<Span<'a>> = Vec::new();

    for (text, is_query_match) in segments {
        if view_pattern.is_empty() {
            // No view search - just apply query highlight or base
            let style = if is_query_match { query_highlight } else { base_style };
            spans.push(Span::styled(text, style));
        } else {
            // Apply view search highlighting within this segment
            let segment_base = if is_query_match { query_highlight } else { base_style };
            let pattern_lower = view_pattern.to_lowercase();
            let text_lower = text.to_lowercase();
            let text_chars: Vec<char> = text.chars().collect();
            let pattern_chars: Vec<char> = pattern_lower.chars().collect();
            let text_lower_chars: Vec<char> = text_lower.chars().collect();

            let mut last_end = 0;
            let mut i = 0;
            while i + pattern_chars.len() <= text_lower_chars.len() {
                let match_found = (0..pattern_chars.len())
                    .all(|j| text_lower_chars[i + j] == pattern_chars[j]);
                if match_found {
                    if i > last_end {
                        let before: String = text_chars[last_end..i].iter().collect();
                        spans.push(Span::styled(before, segment_base));
                    }
                    let matched: String = text_chars[i..i + pattern_chars.len()].iter().collect();
                    spans.push(Span::styled(matched, view_highlight));
                    last_end = i + pattern_chars.len();
                    i = last_end;
                } else {
                    i += 1;
                }
            }
            if last_end < text_chars.len() {
                let remaining: String = text_chars[last_end..].iter().collect();
                spans.push(Span::styled(remaining, segment_base));
            }
        }
    }

    if spans.is_empty() {
        spans.push(Span::styled(strip_html_tags(html_text), base_style));
    }

    spans
}

/// Highlight search pattern matches in text, returning spans with base and highlight styles
fn highlight_search_in_text<'a>(
    text: &str,
    pattern: &str,
    base_style: Style,
    highlight_style: Style,
) -> Vec<Span<'a>> {
    if pattern.is_empty() {
        return vec![Span::styled(text.to_string(), base_style)];
    }

    let pattern_lower = pattern.to_lowercase();
    let text_lower = text.to_lowercase();
    let mut spans: Vec<Span> = Vec::new();
    let mut last_end = 0;

    // Find all occurrences of pattern (case-insensitive)
    let text_chars: Vec<char> = text.chars().collect();
    let pattern_chars: Vec<char> = pattern_lower.chars().collect();
    let text_lower_chars: Vec<char> = text_lower.chars().collect();

    let mut i = 0;
    while i + pattern_chars.len() <= text_lower_chars.len() {
        let match_found = (0..pattern_chars.len())
            .all(|j| text_lower_chars[i + j] == pattern_chars[j]);

        if match_found {
            // Add text before match
            if i > last_end {
                let before: String = text_chars[last_end..i].iter().collect();
                spans.push(Span::styled(before, base_style));
            }
            // Add highlighted match
            let matched: String = text_chars[i..i + pattern_chars.len()].iter().collect();
            spans.push(Span::styled(matched, highlight_style));
            last_end = i + pattern_chars.len();
            i = last_end;
        } else {
            i += 1;
        }
    }

    // Add remaining text
    if last_end < text_chars.len() {
        let remaining: String = text_chars[last_end..].iter().collect();
        spans.push(Span::styled(remaining, base_style));
    }

    if spans.is_empty() {
        spans.push(Span::styled(text.to_string(), base_style));
    }

    spans
}

/// Parse a flexible date string into (YYYYMMDD, display_format) for comparison and display
/// Accepts: YYYYMMDD, YYYY-MM-DD, MM/DD/YYYY, MM/DD/YY, MM/DD, etc.
/// Returns (comparison_format, display_format) where comparison is YYYYMMDD and display
/// is a user-friendly format like "11/29/25"
fn parse_flexible_date(input: &str) -> Option<(String, String)> {
    use chrono::NaiveDate;

    let input = input.trim();
    if input.is_empty() {
        return None;
    }

    // Try various formats - 2-digit year MUST come before 4-digit for same separator
    // to avoid "11/29/25" being parsed as year 11, month 29, day 25
    let formats = [
        "%Y%m%d",      // 20251129
        "%Y-%m-%d",    // 2025-11-29
        "%m/%d/%y",    // 11/29/25 (2-digit year FIRST for / separator)
        "%m-%d-%y",    // 11-29-25 (2-digit year FIRST for - separator)
        "%m/%d/%Y",    // 11/29/2025
        "%m-%d-%Y",    // 11-29-2025
        "%Y/%m/%d",    // 2025/11/29 (4-digit year LAST for / separator)
    ];

    for fmt in formats {
        if let Ok(date) = NaiveDate::parse_from_str(input, fmt) {
            let comparison = date.format("%Y%m%d").to_string();
            let display = date.format("%m/%d/%y").to_string();
            return Some((comparison, display));
        }
    }

    // Try MM/DD or MM-DD with current year
    let short_formats = ["%m/%d", "%m-%d"];
    let current_year = chrono::Utc::now().format("%Y").to_string();
    for fmt in short_formats {
        if let Ok(date) = NaiveDate::parse_from_str(
            &format!("{}/{}", input, current_year),
            &format!("{}/{}", fmt, "%Y"),
        ) {
            let comparison = date.format("%Y%m%d").to_string();
            let display = date.format("%m/%d/%y").to_string();
            return Some((comparison, display));
        }
    }

    None
}

/// Extract YYYYMMDD from an ISO timestamp for comparison
fn extract_date_for_comparison(timestamp: &str) -> Option<String> {
    // Try to parse as RFC3339 or similar
    if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(timestamp) {
        return Some(dt.format("%Y%m%d").to_string());
    }
    // Try naive datetime
    if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(timestamp, "%Y-%m-%dT%H:%M:%S%.f") {
        return Some(dt.format("%Y%m%d").to_string());
    }
    // Just try to extract YYYY-MM-DD
    if timestamp.len() >= 10 {
        let date_part = &timestamp[..10];
        if let Ok(date) = chrono::NaiveDate::parse_from_str(date_part, "%Y-%m-%d") {
            return Some(date.format("%Y%m%d").to_string());
        }
    }
    None
}

fn wrap_text(text: &str, max_width: usize) -> Vec<String> {
    let mut result = Vec::new();
    for line in text.lines() {
        if line.trim().is_empty() {
            result.push(String::new());
            continue;
        }
        let mut current = String::new();
        let mut width = 0;
        for word in line.split_whitespace() {
            let word_len = word.chars().count();
            if width == 0 {
                current = word.to_string();
                width = word_len;
            } else if width + 1 + word_len <= max_width {
                current.push(' ');
                current.push_str(word);
                width += 1 + word_len;
            } else {
                result.push(current);
                current = word.to_string();
                width = word_len;
            }
        }
        if !current.is_empty() {
            result.push(current);
        }
    }
    if result.is_empty() {
        result.push(String::new());
    }
    result
}

fn format_time_ago(modified: &str) -> String {
    let Ok(dt) = DateTime::parse_from_rfc3339(modified)
        .or_else(|_| {
            // Try parsing ISO format without timezone
            chrono::NaiveDateTime::parse_from_str(modified, "%Y-%m-%dT%H:%M:%S%.f")
                .map(|ndt| Utc.from_utc_datetime(&ndt).fixed_offset())
        })
    else {
        return modified.to_string();
    };

    let now = Utc::now();
    let duration = now.signed_duration_since(dt);

    if duration.num_minutes() < 1 {
        "just now".to_string()
    } else if duration.num_minutes() < 60 {
        format!("{}m ago", duration.num_minutes())
    } else if duration.num_hours() < 24 {
        format!("{}h ago", duration.num_hours())
    } else if duration.num_days() < 7 {
        format!("{}d ago", duration.num_days())
    } else if duration.num_weeks() < 4 {
        format!("{}w ago", duration.num_weeks())
    } else {
        dt.format("%b %d").to_string()
    }
}

// ============================================================================
// Index Loading
// ============================================================================

fn load_sessions(index_path: &str, limit: usize) -> Result<Vec<Session>> {
    // Open index FIRST, then get schema from it (not build our own!)
    let index = Index::open_in_dir(index_path)
        .context("Failed to open index. Run 'aichat build-index' first.")?;

    let schema = index.schema();

    // Look up fields by name from the actual index schema
    let session_id_field = schema.get_field("session_id").context("missing session_id")?;
    let agent_field = schema.get_field("agent").context("missing agent")?;
    let project_field = schema.get_field("project").context("missing project")?;
    let branch_field = schema.get_field("branch").context("missing branch")?;
    let cwd_field = schema.get_field("cwd").context("missing cwd")?;
    let created_field = schema.get_field("created").context("missing created")?;
    let modified_field = schema.get_field("modified").context("missing modified")?;
    let modified_ts_field = schema.get_field("modified_ts").context("missing modified_ts")?;
    let lines_field = schema.get_field("lines").context("missing lines")?;
    let export_path_field = schema.get_field("export_path").context("missing export_path")?;
    let first_msg_role_field = schema.get_field("first_msg_role").context("missing first_msg_role")?;
    let first_msg_content_field = schema.get_field("first_msg_content").context("missing first_msg_content")?;
    let last_msg_role_field = schema.get_field("last_msg_role").context("missing last_msg_role")?;
    let last_msg_content_field = schema.get_field("last_msg_content").context("missing last_msg_content")?;
    let derivation_type_field = schema.get_field("derivation_type").context("missing derivation_type")?;
    let is_sidechain_field = schema.get_field("is_sidechain").context("missing is_sidechain")?;
    // claude_home may not exist in older indexes, so make it optional
    let claude_home_field = schema.get_field("claude_home").ok();
    // custom_title may not exist in older indexes, so make it optional
    let custom_title_field = schema.get_field("custom_title").ok();

    let reader = index
        .reader_builder()
        .reload_policy(ReloadPolicy::OnCommitWithDelay)
        .try_into()
        .context("Failed to create reader")?;

    let searcher = reader.searcher();
    let top_docs = searcher
        .search(&AllQuery, &TopDocs::with_limit(limit * 2))
        .context("Search failed")?;

    let mut sessions = Vec::new();
    for (_score, doc_address) in top_docs {
        let doc: tantivy::TantivyDocument = searcher.doc(doc_address)?;

        let get_text = |field| -> String {
            doc.get_first(field)
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string()
        };

        let lines = doc
            .get_first(lines_field)
            .and_then(|v| v.as_i64())
            .unwrap_or(0);

        let modified_ts = doc
            .get_first(modified_ts_field)
            .and_then(|v| v.as_u64())
            .unwrap_or(0);

        let is_sidechain_str = get_text(is_sidechain_field);

        // Get claude_home if field exists, otherwise empty string
        let claude_home = claude_home_field
            .map(|f| get_text(f))
            .unwrap_or_default();

        // Get custom_title if field exists, otherwise empty string
        let custom_title = custom_title_field
            .map(|f| get_text(f))
            .unwrap_or_default();

        sessions.push(Session {
            session_id: get_text(session_id_field),
            agent: get_text(agent_field),
            project: get_text(project_field),
            branch: get_text(branch_field),
            cwd: get_text(cwd_field),
            created: get_text(created_field),
            modified: get_text(modified_field),
            modified_ts,
            lines,
            export_path: get_text(export_path_field),
            first_msg_role: get_text(first_msg_role_field),
            first_msg_content: get_text(first_msg_content_field),
            last_msg_role: get_text(last_msg_role_field),
            last_msg_content: get_text(last_msg_content_field),
            derivation_type: get_text(derivation_type_field),
            is_sidechain: is_sidechain_str == "true",
            claude_home,
            custom_title,
        });
    }

    // Sort by modified_ts (numeric epoch ms) for reliable time ordering
    sessions.sort_by(|a, b| b.modified_ts.cmp(&a.modified_ts));
    sessions.truncate(limit);

    Ok(sessions)
}

/// Search Tantivy index for sessions matching keyword query.
/// Returns (snippets_map, ranked_session_ids) where:
/// - snippets_map: session_id -> snippet for lookup
/// - ranked_session_ids: session_ids in score order (highest first)
fn search_tantivy(
    index_path: &str,
    query_str: &str,
    filter_claude_home: Option<&str>,
    filter_codex_home: Option<&str>,
) -> (HashMap<String, String>, Vec<String>) {
    // Return empty if query is empty
    if query_str.trim().is_empty() {
        return (HashMap::new(), Vec::new());
    }

    let result: Option<(HashMap<String, String>, Vec<String>)> = (|| {
        let index = Index::open_in_dir(index_path).ok()?;
        let schema = index.schema();

        // Get fields for search and ranking
        let content_field = schema.get_field("content").ok()?;
        let session_id_field = schema.get_field("session_id").ok()?;
        let modified_field = schema.get_field("modified").ok()?;
        let claude_home_field = schema.get_field("claude_home").ok();

        let reader = index
            .reader_builder()
            .reload_policy(ReloadPolicy::OnCommitWithDelay)
            .try_into()
            .ok()?;
        let searcher = reader.searcher();

        // Create query parser for content field
        let query_parser = QueryParser::for_index(&index, vec![content_field]);

        // Parse the base query with lenient parsing
        let base_query = query_parser.parse_query_lenient(query_str).0;

        // Phrase boosting: multi-word queries get 5x boost for exact phrase match
        let words: Vec<&str> = query_str.split_whitespace().collect();
        let content_query: Box<dyn tantivy::query::Query> = if words.len() > 1 {
            // Create phrase query for exact match
            let terms: Vec<Term> = words
                .iter()
                .map(|w| Term::from_field_text(content_field, &w.to_lowercase()))
                .collect();
            let phrase_query = PhraseQuery::new(terms);
            let boosted_phrase = BoostQuery::new(Box::new(phrase_query), 5.0);

            // Combine: boosted phrase OR base query
            Box::new(BooleanQuery::new(vec![
                (Occur::Should, Box::new(boosted_phrase) as Box<dyn tantivy::query::Query>),
                (Occur::Should, Box::new(base_query) as Box<dyn tantivy::query::Query>),
            ]))
        } else {
            Box::new(base_query)
        };

        // Build final query with claude_home filter if field exists and filters provided
        let final_query: Box<dyn tantivy::query::Query> = if let Some(home_field) = claude_home_field {
            // Build home filter: match either claude_home OR codex_home
            let mut home_clauses: Vec<(Occur, Box<dyn tantivy::query::Query>)> = Vec::new();

            if let Some(ch) = filter_claude_home {
                let term = Term::from_field_text(home_field, ch);
                home_clauses.push((Occur::Should, Box::new(TermQuery::new(term, IndexRecordOption::Basic))));
            }
            if let Some(cx) = filter_codex_home {
                let term = Term::from_field_text(home_field, cx);
                home_clauses.push((Occur::Should, Box::new(TermQuery::new(term, IndexRecordOption::Basic))));
            }

            if home_clauses.is_empty() {
                // No home filter specified, just use content query
                content_query
            } else {
                // Combine: content query AND (claude_home OR codex_home)
                let home_filter = BooleanQuery::new(home_clauses);
                Box::new(BooleanQuery::new(vec![
                    (Occur::Must, content_query),
                    (Occur::Must, Box::new(home_filter) as Box<dyn tantivy::query::Query>),
                ]))
            }
        } else {
            // No claude_home field in schema, just use content query
            content_query
        };

        // Search with high limit
        let top_docs = searcher.search(&*final_query, &TopDocs::with_limit(2000)).ok()?;

        // Create snippet generator from the query (re-parse since base_query was moved)
        let snippet_query = query_parser.parse_query_lenient(query_str).0;
        let snippet_generator: Option<SnippetGenerator> = SnippetGenerator::create(&searcher, &*snippet_query, content_field)
            .ok()
            .map(|mut g| { g.set_max_num_chars(200); g });

        // Fallback: extract keywords for manual snippet extraction if generator unavailable
        let query_clean = query_str.trim_matches('"').trim_matches('\'');
        let query_lower = query_clean.to_lowercase();
        let keywords: Vec<&str> = query_lower.split_whitespace().collect();

        // Recency ranking: 7-day half-life exponential decay
        let now = Utc::now().timestamp() as f64;
        let half_life_secs = 7.0 * 24.0 * 3600.0; // 7 days

        // Collect results with scores and apply recency boost
        let mut scored_results: Vec<(f32, String, String)> = top_docs
            .iter()
            .filter_map(|(score, doc_address)| {
                let doc: tantivy::TantivyDocument = searcher.doc(*doc_address).ok()?;
                let session_id = doc.get_first(session_id_field)?.as_str()?.to_string();
                let content = doc.get_first(content_field)?.as_str()?;
                let modified = doc.get_first(modified_field)?.as_str().unwrap_or("");

                // Parse modified timestamp and compute recency boost
                let modified_ts = DateTime::parse_from_rfc3339(modified)
                    .map(|dt| dt.timestamp() as f64)
                    .unwrap_or(0.0);
                let age = (now - modified_ts).max(0.0);
                let recency_mult = 1.0 + (-age / half_life_secs).exp();

                let final_score = *score * recency_mult as f32;
                // Use Tantivy's snippet generator if available, else fallback to manual extraction
                // Keep <b> tags for highlighting - they'll be parsed when rendering
                // For multi-word queries WITHOUT quotes, this is an OR search - highlight any keyword
                let is_multi_word = keywords.len() > 1;
                let snippet = if let Some(ref gen) = snippet_generator {
                    let tantivy_snippet = gen.snippet(content);
                    let html = tantivy_snippet.to_html();
                    if html.is_empty() {
                        // Fallback if Tantivy snippet is empty
                        extract_snippet(content, &keywords, 100)
                    } else if is_multi_word {
                        // Multi-word query: Tantivy's highlighting is unreliable for OR queries
                        // Re-highlight all keywords (case-insensitive, substring-aware)
                        rehighlight_keywords(&html, &keywords)
                    } else if html.contains("<b>") {
                        // Single keyword: Tantivy found something to highlight - use it
                        merge_adjacent_highlights(&html)
                    } else {
                        // Tantivy returned text but no highlights - use custom extraction
                        extract_snippet(content, &keywords, 100)
                    }
                } else {
                    extract_snippet(content, &keywords, 100)
                };
                Some((final_score, session_id, snippet))
            })
            .collect();

        // Re-sort by final score (descending) - recency-adjusted ranking
        scored_results.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));

        // Build both the snippet map and the ranked ID list
        let mut snippets: HashMap<String, String> = HashMap::new();
        let mut ranked_ids: Vec<String> = Vec::new();
        for (_, id, snippet) in scored_results {
            ranked_ids.push(id.clone());
            snippets.insert(id, snippet);
        }

        Some((snippets, ranked_ids))
    })();

    result.unwrap_or_default()
}

/// Re-highlight all keywords in a snippet (case-insensitive, including substrings).
/// This fixes Tantivy's SnippetGenerator which doesn't always highlight all occurrences
/// for multi-term queries.
fn rehighlight_keywords(snippet: &str, keywords: &[&str]) -> String {
    // First strip any existing <b> tags to get plain text
    let plain = strip_html_tags(snippet);
    let plain_lower = plain.to_lowercase();
    let plain_chars: Vec<char> = plain.chars().collect();
    let plain_lower_chars: Vec<char> = plain_lower.chars().collect();

    // Find all keyword positions (case-insensitive, substring matching)
    let mut highlights: Vec<(usize, usize)> = Vec::new();
    for keyword in keywords {
        let kw_lower = keyword.to_lowercase();
        let kw_chars: Vec<char> = kw_lower.chars().collect();
        if kw_chars.is_empty() {
            continue;
        }
        let mut search_pos = 0;
        while search_pos + kw_chars.len() <= plain_lower_chars.len() {
            let match_found = (0..kw_chars.len())
                .all(|i| plain_lower_chars.get(search_pos + i) == Some(&kw_chars[i]));
            if match_found {
                highlights.push((search_pos, search_pos + kw_chars.len()));
                search_pos += kw_chars.len();
            } else {
                search_pos += 1;
            }
        }
    }

    if highlights.is_empty() {
        return plain;
    }

    // Sort and merge overlapping highlights
    highlights.sort_by_key(|h| h.0);
    let mut merged: Vec<(usize, usize)> = Vec::new();
    for (start, end) in highlights {
        if let Some(last) = merged.last_mut() {
            if start <= last.1 {
                last.1 = last.1.max(end);
                continue;
            }
        }
        merged.push((start, end));
    }

    // Build result with <b> tags
    let mut result = String::new();
    let mut current_pos = 0;
    for (start, end) in merged {
        if start > current_pos {
            result.push_str(&plain_chars[current_pos..start].iter().collect::<String>());
        }
        result.push_str("<b>");
        result.push_str(&plain_chars[start..end].iter().collect::<String>());
        result.push_str("</b>");
        current_pos = end;
    }
    if current_pos < plain_chars.len() {
        result.push_str(&plain_chars[current_pos..].iter().collect::<String>());
    }
    result
}

/// Extract a snippet from content containing the keywords.
/// For multi-word queries, prioritizes finding the exact phrase over scattered keywords.
/// Returns a window of text around the best match with HTML highlighting.
fn extract_snippet(content: &str, keywords: &[&str], window_chars: usize) -> String {
    let content_lower = content.to_lowercase();
    let chars: Vec<char> = content.chars().collect();
    let chars_lower: Vec<char> = content_lower.chars().collect();

    // Helper to build snippet around a character position (returns plain text)
    let build_snippet_text = |match_start: usize, match_len: usize| -> (String, bool, bool) {
        let half_window = window_chars / 2;
        let start_idx = match_start.saturating_sub(half_window);
        let end_idx = (match_start + match_len + half_window).min(chars.len());

        // Find word boundaries (whitespace)
        let snippet_start = (0..start_idx)
            .rev()
            .find(|&idx| chars[idx].is_whitespace())
            .map(|idx| idx + 1)
            .unwrap_or(start_idx);
        let snippet_end = (end_idx..chars.len())
            .find(|&idx| chars[idx].is_whitespace())
            .unwrap_or(end_idx);

        let snippet_text: String = chars[snippet_start..snippet_end].iter().collect();
        (snippet_text.trim().to_string(), snippet_start > 0, snippet_end < chars.len())
    };

    // Helper to add <b> tags around keywords in a snippet
    let highlight_keywords = |text: &str, has_prefix: bool, has_suffix: bool| -> String {
        let text_lower = text.to_lowercase();
        let text_chars: Vec<char> = text.chars().collect();
        let text_lower_chars: Vec<char> = text_lower.chars().collect();

        // Find all keyword positions
        let mut highlights: Vec<(usize, usize)> = Vec::new();
        for keyword in keywords {
            let kw_chars: Vec<char> = keyword.chars().collect();
            if kw_chars.is_empty() {
                continue;
            }
            let mut search_pos = 0;
            while search_pos + kw_chars.len() <= text_lower_chars.len() {
                let match_found = (0..kw_chars.len())
                    .all(|i| text_lower_chars.get(search_pos + i) == Some(&kw_chars[i]));
                if match_found {
                    highlights.push((search_pos, search_pos + kw_chars.len()));
                    search_pos += kw_chars.len();
                } else {
                    search_pos += 1;
                }
            }
        }

        // Sort and merge overlapping highlights
        highlights.sort_by_key(|h| h.0);
        let mut merged: Vec<(usize, usize)> = Vec::new();
        for (start, end) in highlights {
            if let Some(last) = merged.last_mut() {
                if start <= last.1 {
                    last.1 = last.1.max(end);
                    continue;
                }
            }
            merged.push((start, end));
        }

        // Build result with <b> tags
        let mut result = String::new();
        if has_prefix {
            result.push_str("...");
        }
        let mut current_pos = 0;
        for (start, end) in merged {
            if start > current_pos {
                result.push_str(&text_chars[current_pos..start].iter().collect::<String>());
            }
            result.push_str("<b>");
            result.push_str(&text_chars[start..end].iter().collect::<String>());
            result.push_str("</b>");
            current_pos = end;
        }
        if current_pos < text_chars.len() {
            result.push_str(&text_chars[current_pos..].iter().collect::<String>());
        }
        if has_suffix {
            result.push_str("...");
        }
        result
    };

    // For multi-word queries, first try to find the exact phrase
    if keywords.len() > 1 {
        let phrase = keywords.join(" ");
        let phrase_chars: Vec<char> = phrase.chars().collect();
        for i in 0..chars_lower.len().saturating_sub(phrase_chars.len() - 1) {
            let matches = phrase_chars
                .iter()
                .enumerate()
                .all(|(j, &pc)| chars_lower.get(i + j) == Some(&pc));
            if matches {
                let (text, has_prefix, has_suffix) = build_snippet_text(i, phrase_chars.len());
                return highlight_keywords(&text, has_prefix, has_suffix);
            }
        }
    }

    // Fallback: find the first keyword occurrence (by character index)
    for keyword in keywords {
        let kw_chars: Vec<char> = keyword.chars().collect();
        if kw_chars.is_empty() {
            continue;
        }

        // Search for keyword in lowercased char array
        for i in 0..chars_lower.len().saturating_sub(kw_chars.len() - 1) {
            let matches = kw_chars
                .iter()
                .enumerate()
                .all(|(j, &kc)| chars_lower.get(i + j) == Some(&kc));
            if matches {
                let (text, has_prefix, has_suffix) = build_snippet_text(i, kw_chars.len());
                return highlight_keywords(&text, has_prefix, has_suffix);
            }
        }
    }

    // Fallback: return start of content (no highlights)
    let end_idx = window_chars.min(chars.len());
    let snippet_end = (0..end_idx)
        .rev()
        .find(|&idx| chars[idx].is_whitespace())
        .unwrap_or(end_idx);
    let snippet_text: String = chars[..snippet_end].iter().collect();
    format!("{}...", snippet_text)
}

// ============================================================================
// JSONL Parsing for Full Conversation View
// ============================================================================

/// Execute the selected action from the action menu modal.
/// View action is handled in Rust, others set selected_action and quit to Python.
fn execute_action_item(app: &mut App, item: ActionMenuItem) {
    match item {
        ActionMenuItem::View => {
            // View: enter full view mode (stays in Rust)
            if let Some(session) = app.selected_session() {
                let raw_content = std::fs::read_to_string(&session.export_path)
                    .unwrap_or_else(|_| "Error loading content".to_string());
                app.full_content = if session.export_path.ends_with(".jsonl") {
                    parse_jsonl_to_conversation(&raw_content)
                } else {
                    raw_content
                };
                app.full_content_scroll = 0;
                app.full_view_mode = true;
                app.view_search_mode = false;
                app.view_search_pattern.clear();
                app.view_search_matches.clear();
                app.view_search_current = 0;

                // Build query match lines using SnippetGenerator
                app.query_match_lines.clear();
                app.query_match_current = 0;
                if !app.query.is_empty() {
                    if let Ok(index) = Index::open_in_dir(&app.index_path) {
                        if let Ok(content_field) = index.schema().get_field("content") {
                            let query_parser = QueryParser::for_index(&index, vec![content_field]);
                            let parsed_query = query_parser.parse_query_lenient(&app.query).0;
                            if let Ok(reader) = index.reader() {
                                let searcher = reader.searcher();
                                if let Ok(mut gen) = SnippetGenerator::create(&searcher, &*parsed_query, content_field) {
                                    gen.set_max_num_chars(10000);
                                    for (idx, line) in app.full_content.lines().enumerate() {
                                        let html = gen.snippet(line).to_html();
                                        if html.contains("<b>") {
                                            app.query_match_lines.push(idx);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            app.action_mode = None;
            app.action_modal_selected = 0;
        }
        ActionMenuItem::CopyId => {
            // Copy session ID to clipboard (handled in Rust)
            if let Some(session) = app.selected_session() {
                match arboard::Clipboard::new() {
                    Ok(mut clipboard) => {
                        if clipboard.set_text(&session.session_id).is_ok() {
                            app.status_message =
                                Some(format!("Copied: {}", session.session_id));
                        } else {
                            app.status_message = Some("Failed to copy to clipboard".to_string());
                        }
                    }
                    Err(_) => {
                        app.status_message = Some("Clipboard not available".to_string());
                    }
                }
            }
            app.action_mode = None;
            app.action_modal_selected = 0;
        }
        ActionMenuItem::Delete => {
            // Delete: show confirmation modal before executing
            app.confirming_delete = true;
            app.action_mode = None;
            app.action_modal_selected = 0;
        }
        _ => {
            // All other actions: hand off to Python/Node
            if let Some(session) = app.selected_session() {
                app.should_select = Some(session.clone());
                app.selected_action = Some(item.action_string().to_string());
                app.should_quit = true;
            }
            app.action_mode = None;
            app.action_modal_selected = 0;
        }
    }
}

/// Parse JSONL file content into conversational text format.
/// Handles both Claude and Codex JSONL formats.
/// Returns text with "> " prefix for user messages and "⏺ " for assistant messages.
fn parse_jsonl_to_conversation(content: &str) -> String {
    let mut output = String::new();
    let mut last_role: Option<String> = None;

    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        // Parse JSON line
        let json: serde_json::Value = match serde_json::from_str(line) {
            Ok(v) => v,
            Err(_) => continue,
        };

        // Try to extract message based on format
        let (role, text) = extract_message_from_json(&json);

        if let (Some(role), Some(text)) = (role, text) {
            // Skip empty messages
            if text.trim().is_empty() {
                continue;
            }

            // Add blank line between different roles
            if let Some(ref last) = last_role {
                if last != &role && !output.is_empty() {
                    output.push('\n');
                }
            }

            // Format based on role
            let prefix = if role == "user" { "> " } else { "⏺ " };

            // Split text into lines and prefix the first line
            let lines: Vec<&str> = text.lines().collect();
            for (i, line) in lines.iter().enumerate() {
                if i == 0 {
                    output.push_str(prefix);
                    output.push_str(line);
                } else {
                    // Continuation lines - indent to align with content
                    output.push_str("  ");
                    output.push_str(line);
                }
                output.push('\n');
            }

            last_role = Some(role);
        }
    }

    output
}

/// Extract role and text from a JSON entry (handles Claude and Codex formats).
fn extract_message_from_json(json: &serde_json::Value) -> (Option<String>, Option<String>) {
    let entry_type = json.get("type").and_then(|v| v.as_str());

    match entry_type {
        // Claude format: {"type": "user" | "assistant", "message": {...}}
        Some("user") | Some("assistant") => {
            let role = entry_type.map(|s| s.to_string());
            let text = extract_claude_message_text(json);
            (role, text)
        }

        // Codex format: {"type": "response_item", "payload": {"role": "user" | "assistant", ...}}
        Some("response_item") => {
            if let Some(payload) = json.get("payload") {
                let role = payload
                    .get("role")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                let text = extract_codex_message_text(payload);
                (role, text)
            } else {
                (None, None)
            }
        }

        // Codex format: {"type": "event_msg", "payload": {"type": "user_message", "message": "..."}}
        Some("event_msg") => {
            if let Some(payload) = json.get("payload") {
                let msg_type = payload.get("type").and_then(|v| v.as_str());
                match msg_type {
                    Some("user_message") => {
                        let text = payload
                            .get("message")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string());
                        (Some("user".to_string()), text)
                    }
                    _ => (None, None),
                }
            } else {
                (None, None)
            }
        }

        _ => (None, None),
    }
}

/// Extract text from Claude message format.
/// User: {"message": {"content": "text"}}
/// Assistant: {"message": {"content": [{"type": "text", "text": "..."}]}}
fn extract_claude_message_text(json: &serde_json::Value) -> Option<String> {
    let message = json.get("message")?;
    let content = message.get("content")?;

    // User messages have string content
    if let Some(text) = content.as_str() {
        return Some(text.to_string());
    }

    // Assistant messages have array of content blocks
    if let Some(blocks) = content.as_array() {
        let mut texts = Vec::new();
        for block in blocks {
            if let Some(block_type) = block.get("type").and_then(|v| v.as_str()) {
                match block_type {
                    "text" => {
                        if let Some(text) = block.get("text").and_then(|v| v.as_str()) {
                            texts.push(text.to_string());
                        }
                    }
                    "tool_use" => {
                        // Optionally show tool calls (condensed)
                        if let Some(name) = block.get("name").and_then(|v| v.as_str()) {
                            texts.push(format!("[Tool: {}]", name));
                        }
                    }
                    _ => {}
                }
            }
        }
        if !texts.is_empty() {
            return Some(texts.join("\n"));
        }
    }

    None
}

/// Extract text from Codex message format.
/// {"content": [{"type": "input_text" | "output_text", "text": "..."}]}
fn extract_codex_message_text(payload: &serde_json::Value) -> Option<String> {
    let content = payload.get("content")?.as_array()?;

    let mut texts = Vec::new();
    for block in content {
        if let Some(block_type) = block.get("type").and_then(|v| v.as_str()) {
            match block_type {
                "input_text" | "output_text" => {
                    if let Some(text) = block.get("text").and_then(|v| v.as_str()) {
                        texts.push(text.to_string());
                    }
                }
                "tool_use" | "function_call" => {
                    if let Some(name) = block.get("name").and_then(|v| v.as_str()) {
                        texts.push(format!("[Tool: {}]", name));
                    }
                }
                _ => {}
            }
        }
    }

    if !texts.is_empty() {
        Some(texts.join("\n"))
    } else {
        None
    }
}

// ============================================================================
// JSON Output
// ============================================================================

fn output_json(app: &App, limit: Option<usize>) -> Result<()> {
    use serde_json::json;

    // Output as JSONL (one JSON object per line) for easy piping and jq processing
    for &idx in app.filtered.iter().take(limit.unwrap_or(usize::MAX)) {
        let s = &app.sessions[idx];
        let obj = json!({
            "session_id": s.session_id,
            "agent": s.agent,
            "project": s.project,
            "branch": s.branch,
            "cwd": s.cwd,
            "lines": s.lines,
            "created": s.created,
            "modified": s.modified,
            "first_msg": s.first_msg_content,
            "last_msg": s.last_msg_content,
            "file_path": s.export_path,
            "derivation_type": s.derivation_type,
            "is_sidechain": s.is_sidechain,
            "custom_title": s.custom_title,
            "snippet": app.search_snippets.get(&s.session_id).map(|s| strip_html_tags(s)),
        });
        println!("{}", serde_json::to_string(&obj)?);
    }
    Ok(())
}

// CLI Options
// ============================================================================

struct CliOptions {
    output_file: Option<std::path::PathBuf>,
    claude_home: Option<String>,
    codex_home: Option<String>,
    global_search: bool,
    filter_dir: Option<String>, // --dir: filter to specific directory (overrides -g)
    num_results: Option<usize>,
    // Subtractive flags: --no-original, --no-trimmed, --no-rollover exclude types from defaults
    no_original: bool,
    no_trimmed: bool,
    no_rollover: bool,
    // Additive flag: --sub-agent adds sub-agents to defaults
    include_sub: bool,
    min_lines: Option<i64>,
    after_date: Option<String>,
    before_date: Option<String>,
    agent_filter: Option<String>,
    query: Option<String>,
    json_output: bool,
    sort_by_time: bool,  // --by-time: sort by last-modified time instead of relevance
    filter_branch: Option<String>, // --branch: filter to specific git branch
    // Scroll/selection state restoration
    selected: Option<usize>,    // --selected: restore selected row index
    list_scroll: Option<usize>, // --scroll: restore scroll offset
}

fn parse_cli_args() -> CliOptions {
    let args: Vec<String> = std::env::args().collect();

    // Helper to get value after a flag
    let get_arg_value = |flag: &str| -> Option<String> {
        args.iter()
            .position(|a| a == flag)
            .and_then(|i| args.get(i + 1))
            .map(|s| s.to_string())
    };

    // Helper to check if flag exists
    let has_flag = |flag: &str| -> bool {
        args.iter().any(|a| a == flag)
    };

    // Output file is the LAST positional arg that's a path (contains / or ends with .json)
    // Using rfind to get the last match, avoiding --claude-home/--codex-home values
    let output_file = args.iter()
        .skip(1)  // skip binary name
        .filter(|a| !a.starts_with('-') && (a.contains('/') || a.ends_with(".json")))
        .last()
        .map(std::path::PathBuf::from);

    let claude_home = get_arg_value("--claude-home")
        .or_else(|| std::env::var("CLAUDE_CONFIG_DIR").ok())
        .or_else(|| {
            dirs::home_dir().map(|h| h.join(".claude").to_string_lossy().to_string())
        });

    let codex_home = get_arg_value("--codex-home")
        .or_else(|| std::env::var("CODEX_HOME").ok())
        .or_else(|| {
            dirs::home_dir().map(|h| h.join(".codex").to_string_lossy().to_string())
        });

    let global_search = has_flag("--global") || has_flag("-g");

    // --dir overrides -g: filter to specific directory
    // Format: --dir path or --dir path:branch
    let dir_arg = get_arg_value("--dir");
    let (filter_dir, branch_from_dir) = if let Some(ref dir) = dir_arg {
        // Parse dir:branch format (use rfind to handle paths with colons)
        let (dir_part, branch_part) = if let Some(colon_idx) = dir.rfind(':') {
            // Only treat as branch separator if what follows looks like a branch name
            // (no slashes) and what precedes is a valid path
            let before = &dir[..colon_idx];
            let after = &dir[colon_idx + 1..];
            if !after.contains('/') && !before.is_empty() {
                (before.to_string(), Some(after.to_string()))
            } else {
                (dir.clone(), None)
            }
        } else {
            (dir.clone(), None)
        };

        // Expand ~ to home directory
        let expanded_dir = if dir_part.starts_with('~') {
            let home = std::env::var("HOME").unwrap_or_default();
            format!("{}{}", home, &dir_part[1..])
        } else if dir_part.starts_with('/') {
            dir_part
        } else {
            // Relative path - make absolute from cwd
            let cwd = std::env::current_dir()
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_default();
            format!("{}/{}", cwd, dir_part)
        };
        (Some(expanded_dir), branch_part)
    } else {
        (None, None)
    };

    let num_results = get_arg_value("--num-results")
        .or_else(|| get_arg_value("-n"))
        .and_then(|s| s.parse().ok());

    // Subtractive flags: --no-original, --no-trimmed, --no-rollover
    // By default all types are shown (except sub-agents); these flags exclude types
    let no_original = has_flag("--no-original");
    let no_trimmed = has_flag("--no-trimmed");
    let no_rollover = has_flag("--no-rollover");
    // Additive flag: --sub-agent adds sub-agents to defaults
    let include_sub = has_flag("--sub-agent");

    let min_lines = get_arg_value("--min-lines")
        .and_then(|s| s.parse().ok());

    let after_date = get_arg_value("--after");
    let before_date = get_arg_value("--before");

    let agent_filter = get_arg_value("--agent");

    let query = get_arg_value("--query");

    let json_output = has_flag("--json");
    let sort_by_time = has_flag("--by-time");

    // --branch can be specified separately or as part of --dir (dir:branch)
    let filter_branch = get_arg_value("--branch").or(branch_from_dir);

    // Scroll/selection state restoration
    let selected = get_arg_value("--selected")
        .and_then(|s| s.parse().ok());
    let list_scroll = get_arg_value("--scroll")
        .and_then(|s| s.parse().ok());

    CliOptions {
        output_file,
        claude_home,
        codex_home,
        global_search,
        filter_dir,
        num_results,
        no_original,
        no_trimmed,
        no_rollover,
        include_sub,
        min_lines,
        after_date,
        before_date,
        agent_filter,
        query,
        json_output,
        sort_by_time,
        filter_branch,
        selected,
        list_scroll,
    }
}

// Main
// ============================================================================

fn main() -> Result<()> {
    let cli = parse_cli_args();

    let index_path = dirs::home_dir()
        .context("Could not find home directory")?
        .join(".cctools")
        .join("search-index");

    const SESSION_LIMIT: usize = 100_000;
    let sessions = load_sessions(index_path.to_str().unwrap(), SESSION_LIMIT)?;

    // Warn if we hit the limit - sessions may have been truncated
    if sessions.len() >= SESSION_LIMIT && !cli.json_output {
        eprintln!("⚠️  WARNING: Session limit ({}) reached!", SESSION_LIMIT);
        eprintln!("⚠️  Some sessions may have been dropped.");
        eprintln!();
    }

    if sessions.is_empty() {
        if cli.json_output {
            println!("[]");
            return Ok(());
        }
        eprintln!("No sessions found. Run 'aichat search' to auto-index.");
        return Ok(());
    }

    // Show home filters (only for TUI mode)
    if !cli.json_output {
        if let Some(ref home) = cli.claude_home {
            eprintln!("Claude home filter: {}", home);
        }
        if let Some(ref home) = cli.codex_home {
            eprintln!("Codex home filter: {}", home);
        }

        let claude_count = sessions.iter().filter(|s| s.agent != "codex").count();
        let codex_count = sessions.iter().filter(|s| s.agent == "codex").count();
        eprintln!("Sessions in index: {} Claude, {} Codex", claude_count, codex_count);
    }

    // Create app with CLI options pre-configured
    let mut app = App::new_with_options(
        sessions,
        index_path.to_string_lossy().to_string(),
        &cli,
    );

    // JSON output mode - output filtered results and exit
    if cli.json_output {
        return output_json(&app, cli.num_results);
    }

    // Interactive TUI mode
    enable_raw_mode()?;
    let mut stdout = stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    loop {
        terminal.draw(|f| render(f, &mut app))?;

        if app.should_quit {
            break;
        }

        while event::poll(Duration::from_millis(0))? {
            if let Event::Key(key) = event::read()? {
                if key.kind == KeyEventKind::Press {
                    // Clear status message on any keypress
                    app.status_message = None;

                    // Handle exit confirmation dialog
                    if app.confirming_exit {
                        match key.code {
                            KeyCode::Enter | KeyCode::Char('y') | KeyCode::Char('Y') => {
                                app.should_quit = true;
                            }
                            KeyCode::Esc | KeyCode::Char('n') | KeyCode::Char('N') => {
                                app.confirming_exit = false;
                            }
                            _ => {}
                        }
                        continue;
                    }

                    // Handle delete confirmation dialog
                    if app.confirming_delete {
                        match key.code {
                            KeyCode::Enter | KeyCode::Char('y') | KeyCode::Char('Y') => {
                                // Execute delete action
                                if let Some(session) = app.selected_session() {
                                    app.should_select = Some(session.clone());
                                    app.selected_action = Some("delete".to_string());
                                    app.should_quit = true;
                                }
                                app.confirming_delete = false;
                            }
                            KeyCode::Esc | KeyCode::Char('n') | KeyCode::Char('N') => {
                                app.confirming_delete = false;
                            }
                            _ => {}
                        }
                        continue;
                    }

                    // Handle full view mode separately
                    if app.full_view_mode {
                        if app.view_search_mode {
                            // Search input mode
                            match key.code {
                                KeyCode::Esc => {
                                    // Cancel search input, keep existing pattern if any
                                    app.view_search_mode = false;
                                }
                                KeyCode::Enter => {
                                    // Confirm search and jump to first match
                                    app.view_search_mode = false;
                                    if app.view_search_pattern.is_empty() {
                                        // Empty pattern: activate query nav mode (blue/original)
                                        app.query_nav_mode = true;
                                        if !app.query_match_lines.is_empty() {
                                            app.query_match_current = 0;
                                            app.full_content_scroll = app.query_match_lines[0];
                                        }
                                    } else {
                                        // Non-empty pattern: activate view search mode (yellow)
                                        app.query_nav_mode = false;
                                        app.update_view_search_matches();
                                        if !app.view_search_matches.is_empty() {
                                            app.view_search_current = 0;
                                            app.full_content_scroll = app.view_search_matches[0];
                                        }
                                    }
                                }
                                KeyCode::Backspace => {
                                    app.view_search_pattern.pop();
                                }
                                KeyCode::Char(c) => {
                                    app.view_search_pattern.push(c);
                                }
                                _ => {}
                            }
                        } else if !app.view_search_pattern.is_empty() || app.query_nav_mode {
                            // Active search mode - yellow (view search) or blue (query nav)
                            match key.code {
                                KeyCode::Char('f') => {
                                    // Next match
                                    if !app.view_search_pattern.is_empty() {
                                        app.view_search_next();
                                    } else {
                                        app.query_match_next();
                                    }
                                }
                                KeyCode::Char('d') => {
                                    // Prev match
                                    if !app.view_search_pattern.is_empty() {
                                        app.view_search_prev();
                                    } else {
                                        app.query_match_prev();
                                    }
                                }
                                KeyCode::Enter => {
                                    // Enter also goes to next match
                                    if !app.view_search_pattern.is_empty() {
                                        app.view_search_next();
                                    } else {
                                        app.query_match_next();
                                    }
                                }
                                KeyCode::Esc => {
                                    // Clear search/nav mode
                                    app.view_search_pattern.clear();
                                    app.view_search_matches.clear();
                                    app.view_search_current = 0;
                                    app.query_nav_mode = false;
                                }
                                KeyCode::Char('/') => {
                                    // Start new search
                                    app.view_search_pattern.clear();
                                    app.query_nav_mode = false;
                                    app.view_search_mode = true;
                                }
                                KeyCode::Char(' ') | KeyCode::Char('q') => {
                                    // Exit view mode, clear search
                                    app.view_search_pattern.clear();
                                    app.view_search_matches.clear();
                                    app.view_search_mode = false;
                                    app.query_nav_mode = false;
                                    app.full_view_mode = false;
                                }
                                KeyCode::Up | KeyCode::Char('k') => {
                                    app.full_content_scroll = app.full_content_scroll.saturating_sub(1);
                                }
                                KeyCode::Down | KeyCode::Char('j') => {
                                    app.full_content_scroll = app.full_content_scroll.saturating_add(1);
                                }
                                KeyCode::PageUp => {
                                    app.full_content_scroll = app.full_content_scroll.saturating_sub(20);
                                }
                                KeyCode::PageDown => {
                                    app.full_content_scroll = app.full_content_scroll.saturating_add(20);
                                }
                                KeyCode::Home => {
                                    app.full_content_scroll = 0;
                                }
                                KeyCode::End => {
                                    let lines = app.full_content.lines().count();
                                    app.full_content_scroll = lines.saturating_sub(20);
                                }
                                KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                                    app.should_quit = true;
                                }
                                _ => {}
                            }
                        } else {
                            // Normal view mode (no active search) - a/d navigate original query (blue) matches
                            match key.code {
                                KeyCode::Char('f') => {
                                    // Next match (original query)
                                    app.query_match_next();
                                }
                                KeyCode::Char('d') => {
                                    // Prev match (original query)
                                    app.query_match_prev();
                                }
                                KeyCode::Char('/') => {
                                    app.view_search_mode = true;
                                    app.view_search_pattern.clear();
                                }
                                KeyCode::Char(' ') | KeyCode::Esc | KeyCode::Char('q') => {
                                    app.full_view_mode = false;
                                    app.query_nav_mode = false;
                                }
                                KeyCode::Up | KeyCode::Char('k') => {
                                    app.full_content_scroll = app.full_content_scroll.saturating_sub(1);
                                }
                                KeyCode::Down | KeyCode::Char('j') => {
                                    app.full_content_scroll = app.full_content_scroll.saturating_add(1);
                                }
                                KeyCode::PageUp => {
                                    app.full_content_scroll = app.full_content_scroll.saturating_sub(20);
                                }
                                KeyCode::PageDown => {
                                    app.full_content_scroll = app.full_content_scroll.saturating_add(20);
                                }
                                KeyCode::Home => {
                                    app.full_content_scroll = 0;
                                }
                                KeyCode::End => {
                                    let lines = app.full_content.lines().count();
                                    app.full_content_scroll = lines.saturating_sub(20);
                                }
                                KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                                    app.should_quit = true;
                                }
                                _ => {}
                            }
                        }
                    } else if app.scope_modal_open {
                        // Handle scope modal
                        match key.code {
                            KeyCode::Esc => {
                                app.scope_modal_open = false;
                            }
                            KeyCode::Char('/') => {
                                app.scope_modal_open = false;
                            }
                            KeyCode::Up | KeyCode::Char('k') => {
                                if app.scope_modal_selected > 0 {
                                    app.scope_modal_selected -= 1;
                                }
                            }
                            KeyCode::Down | KeyCode::Char('j') => {
                                if app.scope_modal_selected < 2 {
                                    app.scope_modal_selected += 1;
                                }
                            }
                            KeyCode::Enter | KeyCode::Char(' ') => {
                                match app.scope_modal_selected {
                                    0 => {
                                        // Global
                                        app.scope_global = true;
                                        app.filter_dir = None;
                                        app.filter();
                                        app.scope_modal_open = false;
                                    }
                                    1 => {
                                        // Current directory
                                        app.scope_global = false;
                                        app.filter_dir = None;
                                        app.filter();
                                        app.scope_modal_open = false;
                                    }
                                    2 => {
                                        // Custom directory - enter input mode
                                        app.scope_modal_open = false;
                                        app.input_mode = Some(InputMode::ScopeDir);
                                        // Pre-fill with current dir:branch format
                                        let dir = app.filter_dir.clone()
                                            .unwrap_or_else(|| app.launch_cwd.clone());
                                        app.input_buffer = if let Some(ref branch) = app.filter_branch {
                                            format!("{}:{}", dir, branch)
                                        } else {
                                            dir
                                        };
                                    }
                                    _ => {}
                                }
                            }
                            KeyCode::Char('1') => {
                                app.scope_global = true;
                                app.filter_dir = None;
                                app.filter();
                                app.scope_modal_open = false;
                            }
                            KeyCode::Char('2') => {
                                app.scope_global = false;
                                app.filter_dir = None;
                                app.filter();
                                app.scope_modal_open = false;
                            }
                            KeyCode::Char('3') => {
                                // Custom directory - enter input mode
                                app.scope_modal_open = false;
                                app.input_mode = Some(InputMode::ScopeDir);
                                // Pre-fill with current dir:branch format
                                let dir = app.filter_dir.clone()
                                    .unwrap_or_else(|| app.launch_cwd.clone());
                                app.input_buffer = if let Some(ref branch) = app.filter_branch {
                                    format!("{}:{}", dir, branch)
                                } else {
                                    dir
                                };
                            }
                            _ => {}
                        }
                    } else if app.filter_modal_open {
                        // Handle filter modal
                        let items = FilterMenuItem::all();

                        // Helper to apply filter by item
                        let apply_filter = |app: &mut App, item: &FilterMenuItem| {
                            match item {
                                FilterMenuItem::ClearAll => {
                                    // Reset to defaults
                                    app.include_original = true;
                                    app.include_sub = false;
                                    app.include_trimmed = true;
                                    app.include_continued = true;
                                    app.filter_agent = None;
                                    app.filter_min_lines = None;
                                    app.filter();
                                }
                                FilterMenuItem::IncludeOriginal => {
                                    app.include_original = !app.include_original;
                                    app.filter();
                                }
                                FilterMenuItem::IncludeSub => {
                                    app.include_sub = !app.include_sub;
                                    app.filter();
                                }
                                FilterMenuItem::IncludeTrimmed => {
                                    app.include_trimmed = !app.include_trimmed;
                                    app.filter();
                                }
                                FilterMenuItem::IncludeContinued => {
                                    app.include_continued = !app.include_continued;
                                    app.filter();
                                }
                                FilterMenuItem::AgentAll => {
                                    app.filter_agent = None;
                                    app.filter();
                                }
                                FilterMenuItem::AgentClaude => {
                                    app.filter_agent = Some("claude".to_string());
                                    app.filter();
                                }
                                FilterMenuItem::AgentCodex => {
                                    app.filter_agent = Some("codex".to_string());
                                    app.filter();
                                }
                                FilterMenuItem::MinLines => {
                                    app.filter_modal_open = false;
                                    app.input_mode = Some(InputMode::MinLines);
                                    app.input_buffer.clear();
                                }
                                FilterMenuItem::AfterDate => {
                                    app.filter_modal_open = false;
                                    app.input_mode = Some(InputMode::AfterDate);
                                    app.input_buffer.clear();
                                }
                                FilterMenuItem::BeforeDate => {
                                    app.filter_modal_open = false;
                                    app.input_mode = Some(InputMode::BeforeDate);
                                    app.input_buffer.clear();
                                }
                            }
                        };

                        match key.code {
                            KeyCode::Esc => {
                                app.filter_modal_open = false;
                            }
                            KeyCode::Char('f') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                                app.filter_modal_open = false;
                            }
                            KeyCode::Up | KeyCode::Char('k') => {
                                if app.filter_modal_selected > 0 {
                                    app.filter_modal_selected -= 1;
                                }
                            }
                            KeyCode::Down | KeyCode::Char('j') => {
                                if app.filter_modal_selected < items.len() - 1 {
                                    app.filter_modal_selected += 1;
                                }
                            }
                            KeyCode::Enter | KeyCode::Char(' ') => {
                                let item = items[app.filter_modal_selected].clone();
                                apply_filter(&mut app, &item);
                            }
                            // Shortcut keys
                            KeyCode::Char(c) => {
                                if let Some(item) = items.iter().find(|i| i.shortcut() == c) {
                                    apply_filter(&mut app, item);
                                }
                            }
                            _ => {}
                        }
                    } else if app.action_mode.is_some() {
                        // Handle action menu modal
                        let items = ActionMenuItem::all();
                        match key.code {
                            KeyCode::Esc => {
                                app.action_mode = None;
                                app.action_modal_selected = 0;
                            }
                            KeyCode::Up | KeyCode::Char('k') => {
                                if app.action_modal_selected > 0 {
                                    app.action_modal_selected -= 1;
                                }
                            }
                            KeyCode::Down | KeyCode::Char('j') => {
                                if app.action_modal_selected < items.len() - 1 {
                                    app.action_modal_selected += 1;
                                }
                            }
                            KeyCode::Enter | KeyCode::Char(' ') => {
                                // Execute selected action
                                let item = items[app.action_modal_selected].clone();
                                execute_action_item(&mut app, item);
                            }
                            // Single-letter shortcuts move selection to that item
                            KeyCode::Char(c) => {
                                if let Some(idx) = items.iter().position(|i| i.shortcut() == c) {
                                    app.action_modal_selected = idx;
                                }
                            }
                            _ => {}
                        }
                    } else if app.input_mode.is_some() {
                        // Handle input mode for :m and :a
                        let mode = app.input_mode.clone().unwrap();
                        match key.code {
                            KeyCode::Esc => {
                                app.input_mode = None;
                                app.input_buffer.clear();
                            }
                            KeyCode::Enter => {
                                match mode {
                                    InputMode::MinLines => {
                                        if let Ok(num) = app.input_buffer.parse::<i64>() {
                                            app.filter_min_lines = if num > 0 { Some(num) } else { None };
                                            app.filter();
                                        }
                                    }
                                    InputMode::Agent => {}
                                    InputMode::JumpToLine => {
                                        if let Ok(row) = app.input_buffer.parse::<usize>() {
                                            app.jump_to_row(row);
                                        }
                                    }
                                    InputMode::AfterDate => {
                                        if app.input_buffer.is_empty() {
                                            app.filter_after_date = None;
                                            app.filter_after_date_display = None;
                                        } else if let Some((cmp, disp)) = parse_flexible_date(&app.input_buffer) {
                                            app.filter_after_date = Some(cmp);
                                            app.filter_after_date_display = Some(disp);
                                        }
                                        app.filter();
                                    }
                                    InputMode::BeforeDate => {
                                        if app.input_buffer.is_empty() {
                                            app.filter_before_date = None;
                                            app.filter_before_date_display = None;
                                        } else if let Some((cmp, disp)) = parse_flexible_date(&app.input_buffer) {
                                            app.filter_before_date = Some(cmp);
                                            app.filter_before_date_display = Some(disp);
                                        }
                                        app.filter();
                                    }
                                    InputMode::ScopeDir => {
                                        // Parse format: [directory][:branch]
                                        // Examples: "", "/path", ":branch", "/path:branch"
                                        let (dir_part, branch_part) = if let Some(colon_idx) = app.input_buffer.rfind(':') {
                                            // Check if colon is part of a path (e.g., not preceded by ~/ or /)
                                            // Use rfind to get the last colon (branch separator)
                                            let before = &app.input_buffer[..colon_idx];
                                            let after = &app.input_buffer[colon_idx + 1..];
                                            (before.to_string(), Some(after.to_string()))
                                        } else {
                                            (app.input_buffer.clone(), None)
                                        };

                                        // Handle directory part
                                        if dir_part.is_empty() {
                                            // No dir specified = stay in current mode
                                            // If branch_part is None, go global; otherwise keep current dir scope
                                            if branch_part.is_none() {
                                                app.scope_global = true;
                                                app.filter_dir = None;
                                            }
                                            // If only ":branch", keep current directory scope
                                        } else {
                                            // Expand ~ to home directory
                                            let path = if dir_part.starts_with('~') {
                                                let home = std::env::var("HOME").unwrap_or_default();
                                                format!("{}{}", home, &dir_part[1..])
                                            } else if dir_part.starts_with('/') {
                                                dir_part
                                            } else {
                                                // Relative path - make absolute from launch_cwd
                                                format!("{}/{}", app.launch_cwd, dir_part)
                                            };
                                            app.filter_dir = Some(path);
                                            app.scope_global = false;
                                        }

                                        // Handle branch part
                                        if let Some(branch) = branch_part {
                                            if branch.is_empty() {
                                                app.filter_branch = None; // ":empty" clears branch
                                            } else {
                                                app.filter_branch = Some(branch);
                                            }
                                        } else {
                                            // No colon in input = clear branch filter
                                            app.filter_branch = None;
                                        }

                                        app.filter();
                                    }
                                    InputMode::Branch => {
                                        // Legacy - kept for compatibility
                                        if app.input_buffer.is_empty() {
                                            app.filter_branch = None;
                                        } else {
                                            app.filter_branch = Some(app.input_buffer.clone());
                                        }
                                        app.filter();
                                    }
                                }
                                app.input_mode = None;
                                app.input_buffer.clear();
                            }
                            KeyCode::Char('1') if mode == InputMode::Agent => {
                                app.filter_agent = Some("claude".to_string());
                                app.filter();
                                app.input_mode = None;
                                app.input_buffer.clear();
                            }
                            KeyCode::Char('2') if mode == InputMode::Agent => {
                                app.filter_agent = Some("codex".to_string());
                                app.filter();
                                app.input_mode = None;
                                app.input_buffer.clear();
                            }
                            KeyCode::Char('0') if mode == InputMode::Agent => {
                                app.filter_agent = None;
                                app.filter();
                                app.input_mode = None;
                                app.input_buffer.clear();
                            }
                            KeyCode::Char(c) if c.is_ascii_digit() && (mode == InputMode::MinLines || mode == InputMode::JumpToLine) => {
                                app.input_buffer.push(c);
                            }
                            KeyCode::Char(c) if mode == InputMode::AfterDate || mode == InputMode::BeforeDate || mode == InputMode::ScopeDir || mode == InputMode::Branch => {
                                // Accept any character for flexible input
                                app.input_buffer.push(c);
                            }
                            KeyCode::Backspace if mode == InputMode::MinLines || mode == InputMode::JumpToLine || mode == InputMode::AfterDate || mode == InputMode::BeforeDate || mode == InputMode::ScopeDir || mode == InputMode::Branch => {
                                app.input_buffer.pop();
                            }
                            _ => {}
                        }
                    } else if app.command_mode {
                        // Handle command mode (: prefix)
                        app.command_mode = false;
                        match key.code {
                            KeyCode::Char('x') | KeyCode::Char('0') => {
                                // Reset to defaults
                                app.include_original = true;
                                app.include_sub = false;
                                app.include_trimmed = true;
                                app.include_continued = true;
                                app.filter_agent = None;
                                app.filter_min_lines = None;
                                app.filter_after_date = None;
                                app.filter_after_date_display = None;
                                app.filter_before_date = None;
                                app.filter_before_date_display = None;
                                app.filter();
                            }
                            KeyCode::Char('o') => {
                                app.include_original = !app.include_original;
                                app.filter();
                            }
                            KeyCode::Char('s') => {
                                app.include_sub = !app.include_sub;
                                app.filter();
                            }
                            KeyCode::Char('t') => {
                                app.include_trimmed = !app.include_trimmed;
                                app.filter();
                            }
                            KeyCode::Char('c') => {
                                app.include_continued = !app.include_continued;
                                app.filter();
                            }
                            KeyCode::Char('d') => {
                                // Enter agent input mode
                                app.input_mode = Some(InputMode::Agent);
                                app.input_buffer.clear();
                            }
                            KeyCode::Char('m') => {
                                // Enter min-lines input mode
                                app.input_mode = Some(InputMode::MinLines);
                                app.input_buffer.clear();
                            }
                            KeyCode::Char('>') => {
                                // Enter after-date input mode
                                app.input_mode = Some(InputMode::AfterDate);
                                app.input_buffer.clear();
                            }
                            KeyCode::Char('<') => {
                                // Enter before-date input mode
                                app.input_mode = Some(InputMode::BeforeDate);
                                app.input_buffer.clear();
                            }
                            KeyCode::Esc => {} // Just exit command mode
                            _ => {}
                        }
                    } else if !app.jump_input.is_empty() {
                        // Handle jump input mode
                        match key.code {
                            KeyCode::Enter => {
                                app.process_jump_enter();
                            }
                            KeyCode::Esc => {
                                app.jump_input.clear();
                            }
                            KeyCode::Char(c) if c.is_ascii_digit() => {
                                app.jump_input.push(c);
                            }
                            KeyCode::Backspace => {
                                app.jump_input.pop();
                            }
                            _ => {}
                        }
                    } else {
                        // Normal mode
                        match key.code {
                            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                                app.should_quit = true;
                            }
                            KeyCode::Char(':') => {
                                app.command_mode = true;
                            }
                            KeyCode::Char(' ') => {
                                // Space: add to query (for multi-word search)
                                app.on_char(' ');
                            }
                            KeyCode::Esc => app.on_escape(),
                            KeyCode::Enter => {
                                // If there's pending jump input, use it
                                if !app.jump_input.is_empty() {
                                    app.process_jump_enter();
                                } else if app.selected_session().is_some() {
                                    // Enter action mode to choose view or actions
                                    app.action_mode = Some(ActionMode::ActionMenu);
                                }
                            }
                            KeyCode::Up => app.on_up(),
                            KeyCode::Down => app.on_down(),
                            KeyCode::PageUp => app.page_up(10),
                            KeyCode::PageDown => app.page_down(10),
                            KeyCode::Home => {
                                // Jump to first result
                                app.selected = 0;
                                app.preview_scroll = 0;
                            }
                            KeyCode::End => {
                                // Jump to last result
                                if !app.filtered.is_empty() {
                                    app.selected = app.filtered.len() - 1;
                                    app.preview_scroll = 0;
                                }
                            }
                            KeyCode::Char('u') if key.modifiers.contains(KeyModifiers::CONTROL) => app.page_up(10),
                            KeyCode::Backspace => app.on_backspace(),
                            KeyCode::Char('/') => {
                                // Open scope modal
                                app.scope_modal_open = true;
                                app.scope_modal_selected = 0;
                            }
                            KeyCode::Char('f') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                                // Open filter modal
                                app.filter_modal_open = true;
                                app.filter_modal_selected = 0;
                            }
                            KeyCode::Char('g') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                                // Enter jump mode (go to line)
                                app.input_mode = Some(InputMode::JumpToLine);
                                app.input_buffer.clear();
                            }
                            KeyCode::Char('s') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                                // Toggle sort mode: relevance <-> time
                                app.sort_by_time = !app.sort_by_time;
                                app.filter(); // Re-sort results
                            }
                            KeyCode::Char(c) => app.on_char(c),
                            _ => {}
                        }
                    }
                }
            }
        }

        std::thread::sleep(Duration::from_millis(16));
    }

    disable_raw_mode()?;
    execute!(io::stdout(), LeaveAlternateScreen)?;

    if let Some(session) = app.should_select {
        // Output session with action and filter state for Python handler
        let output = serde_json::json!({
            "session": session,
            "action": app.selected_action.as_deref().unwrap_or("menu"),
            "filter_state": {
                "query": app.query,
                "scope_global": app.scope_global,
                "filter_dir": app.filter_dir,
                "include_original": app.include_original,
                "include_sub": app.include_sub,
                "include_trimmed": app.include_trimmed,
                "include_continued": app.include_continued,
                "filter_agent": app.filter_agent,
                "filter_min_lines": app.filter_min_lines,
                "filter_after_date": app.filter_after_date,
                "filter_before_date": app.filter_before_date,
                "filter_branch": app.filter_branch,
                "sort_by_time": app.sort_by_time,
                "selected": app.selected,
                "list_scroll": app.list_scroll,
            }
        });
        let json = serde_json::to_string(&output)?;
        if let Some(ref out_path) = cli.output_file {
            std::fs::write(out_path, &json)?;
        } else {
            println!("{}", json);
        }
    }

    Ok(())
}
