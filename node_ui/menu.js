#!/usr/bin/env node
import fs from 'fs';
import meow from 'meow';
import React, {useState} from 'react';
import {render, Box, Text, useApp, useInput, useStdout} from 'ink';
import SelectInput from 'ink-select-input';
import chalk from 'chalk';
import figures from 'figures';
import {spawnSync} from 'child_process';
import {ACTIONS, filteredActions, RESUME_SUBMENU, TRIM_SUBMENU} from './action_config.js';

const h = React.createElement;

const cli = meow(
  `Usage: node menu.js --data payload.json --out result.json`,
  {
    importMeta: import.meta,
    flags: {
      data: {type: 'string', isRequired: true},
      out: {type: 'string', isRequired: true},
    },
  }
);

const payload = JSON.parse(fs.readFileSync(cli.flags.data, 'utf8'));
const sessions = payload.sessions || [];
const keywords = payload.keywords || [];
const outPath = cli.flags.out;
const focusId = payload.focus_id || null;
const startAction = payload.start_action || false;
const startScreen = payload.start_screen || null;
const rpcPath = payload.rpc_path || null;
const scopeLine = payload.scope_line || '';
const tipLine = payload.tip_line || '';
const selectTarget = payload.select_target || 'action'; // screen after selection
const resultsTitle = payload.results_title || null; // custom title for results
const startZoomed = payload.start_zoomed || false; // start with all rows expanded
const lineageBackTarget = payload.lineage_back_target || 'resume'; // where lineage goes back to
const directAction = payload.direct_action || null; // if set, execute this action immediately after selection
// Find options form data
const findOptions = payload.find_options || {};
const findVariant = payload.find_variant || 'find'; // 'find', 'find-claude', 'find-codex'
// Trim confirmation data (for trim_confirm screen)
const trimInfo = payload.trim_info || {};
// Directory confirmation data (for dir_confirm screen)
const dirInfo = payload.dir_info || {};
const BRANCH_ICON = '';
const DATE_FMT = new Intl.DateTimeFormat('en', {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

// MM/DD format for date display (matches Rust UI)
const DATE_DAY_FMT = new Intl.DateTimeFormat('en', {
  month: '2-digit',
  day: '2-digit',
});

// 24-hour time format HH:MM (matches Rust UI)
const TIME_FMT = new Intl.DateTimeFormat('en', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

// Generate annotation string for session display
// Note: "continued" is the internal derivation_type for what users see as "rolled-over" (r)
const toAnno = (s) => {
  const annos = [];
  if (s.derivation_type === 'continue' || s.derivation_type === 'continuation' || s.derivation_type === 'continued' || s.is_continuation) annos.push('r');
  if (s.is_trimmed || s.derivation_type === 'trim' || s.derivation_type === 'trimmed') annos.push('t');
  if (s.is_sidechain) annos.push('sub');
  return annos.length ? `(${annos.join(',')})` : '';
};

const formatLines = (lines) => (Number.isFinite(lines) ? `${lines} user msgs` : '');

// Minimum valid timestamp (year 2000) - timestamps below this are treated as invalid
// This prevents Unix epoch (0) from showing as "12/31 19:00" in US timezones
const MIN_VALID_TS = 946684800;

// Check if timestamp is valid (finite and reasonable)
const isValidTs = (ts) => Number.isFinite(ts) && ts >= MIN_VALID_TS;

// Format date range to match Rust UI: "MM/DD HH:MM" or "MM/DD - MM/DD HH:MM"
const formatDateRange = (start, end) => {
  const validStart = isValidTs(start);
  const validEnd = isValidTs(end);

  if (validStart && validEnd) {
    const sDay = DATE_DAY_FMT.format(new Date(start * 1000));
    const eDay = DATE_DAY_FMT.format(new Date(end * 1000));
    const eTime = TIME_FMT.format(new Date(end * 1000));
    if (sDay === eDay) return `${eDay} ${eTime}`;
    return `${sDay} - ${eDay} ${eTime}`;
  }
  if (validEnd) {
    const eDay = DATE_DAY_FMT.format(new Date(end * 1000));
    const eTime = TIME_FMT.format(new Date(end * 1000));
    return `${eDay} ${eTime}`;
  }
  if (validStart) {
    const sDay = DATE_DAY_FMT.format(new Date(start * 1000));
    const sTime = TIME_FMT.format(new Date(start * 1000));
    return `${sDay} ${sTime}`;
  }
  return '';
};

const colorize = {
  agent: (txt) => chalk.magenta(txt),
  project: (txt) => chalk.green(txt),
  branch: (txt) => chalk.cyan(txt),
  lines: (txt) => chalk.yellow(txt),
  date: (txt) => chalk.blue(txt),
  title: (txt) => chalk.cyan.bold(txt),
};

/**
 * Format session header line with optional custom title.
 * Returns array of spans for use in h(Text, null, ...spans).
 *
 * @param {Object} session - Session object with agent_display, session_id, lines, etc.
 * @param {string} [idOverride] - Optional pre-computed id string (8 chars)
 * @param {string} [annoOverride] - Optional pre-computed annotation string
 * @param {string} [dateOverride] - Optional pre-computed date string
 * @returns {Array} Array of chalk-styled strings/spans
 */
function formatSessionHeader(session, idOverride, annoOverride, dateOverride) {
  const id = idOverride || (session.session_id || '').slice(0, 8);
  const anno = annoOverride !== undefined ? annoOverride : toAnno(session);
  const date = dateOverride || formatDateRange(session.create_time, session.mod_time);
  const customTitle = session.custom_title;

  const spans = [
    colorize.agent(`[${session.agent_display || 'CLAUDE'}]`), ' ',
  ];

  // Add custom title if present
  if (customTitle) {
    spans.push(colorize.title(`[${customTitle}]`), ' ');
  }

  spans.push(
    chalk.white(id),
    anno ? ` ${chalk.dim(anno)}` : '',
    ' | ',
    colorize.lines(formatLines(session.lines)),
    ' | ',
    colorize.date(date)
  );

  return spans;
}

// Non-TTY fallback: pick first session and resume
if (!process.stdout.isTTY) {
  if (sessions.length) {
    fs.writeFileSync(
      outPath,
      JSON.stringify({session_id: sessions[0].session_id, action: 'resume', kwargs: {}}),
      'utf8'
    );
    process.exit(0);
  }
  process.exit(1);
}

function writeResult(sessionId, action, kwargs = {}) {
  const data = JSON.stringify({session_id: sessionId, action, kwargs});
  const fd = fs.openSync(outPath, 'w');
  fs.writeSync(fd, data);
  fs.fsyncSync(fd);
  fs.closeSync(fd);
}

/**
 * Wrap preview text into multiple lines for display.
 * @param {string} preview - The preview text
 * @param {number} maxLines - Maximum number of lines (default: 6)
 * @param {number} maxWidth - Maximum width per line (default: 80)
 * @returns {string[]} Array of wrapped lines
 */
function wrapPreviewLines(preview, maxLines = 6, maxWidth = 80) {
  if (!preview) return [];
  const words = preview.split(/\s+/);
  const lines = [];
  let currentLine = '';
  for (const word of words) {
    if ((currentLine + ' ' + word).length > maxWidth) {
      if (currentLine) lines.push(currentLine);
      currentLine = word;
      if (lines.length >= maxLines) break;
    } else {
      currentLine = currentLine ? currentLine + ' ' + word : word;
    }
  }
  if (currentLine && lines.length < maxLines) lines.push(currentLine);
  return lines;
}

/**
 * Render preview as Ink elements (Box with multiple Text lines).
 * @param {string} preview - The preview text
 * @param {number} maxLines - Maximum number of lines (default: 6)
 * @param {number} maxWidth - Maximum width per line (default: 80)
 * @param {string} indent - Indentation for each line (default: '  ')
 * @returns {React.Element|null} Ink Box element or null if no preview
 */
function renderPreview(preview, maxLines = 6, maxWidth = 80, indent = '  ') {
  const lines = wrapPreviewLines(preview, maxLines, maxWidth);
  if (lines.length === 0) return null;
  return h(Box, {flexDirection: 'column'},
    h(Text, {dimColor: true}, 'Preview:'),
    ...lines.map((line, i) => h(Text, {key: i, dimColor: true}, indent + line))
  );
}

function SessionRow({session, active, index, width, pad, isExpanded}) {
  const id = (session.session_id || '').slice(0, 8);
  const anno = toAnno(session);
  const lines = formatLines(session.lines);
  const date = formatDateRange(session.create_time, session.mod_time);
  const preview = session.preview || '';
  const maxPreview = Math.max(0, width - 30);
  const trimmedPreview = preview.length > maxPreview
    ? preview.slice(0, maxPreview - 1) + '…'
    : preview;

  const agentIdRaw = `[${session.agent_display}] ${id}${anno ? ' ' + anno : ''}`;
  const agentIdPart = colorize.agent(agentIdRaw.padEnd(pad.agentId));
  const projPart = colorize.project((session.project || '').padEnd(pad.project));
  const branchRaw = session.branch ? `${BRANCH_ICON} ${session.branch}` : '';
  const branchPart = colorize.branch(branchRaw.padEnd(pad.branch));
  const linesPart = colorize.lines((lines || '').padEnd(pad.lines));
  const datePart = colorize.date((date || '').padEnd(pad.date));
  // Show [−] when expanded, [+] when collapsed (dimmed to match line brightness)
  const expandIcon = chalk.dim(isExpanded ? '[−]' : '[+]');
  const header = `${(index + 1).toString().padEnd(2)} ${agentIdPart} | ${projPart} | ${branchPart} | ${linesPart} | ${datePart} ${expandIcon}`;

  // When expanded, show up to 4 lines of preview; when collapsed, show 1 line
  const previewLines = [];
  if (preview) {
    if (isExpanded) {
      // Split preview into multiple lines, up to 4
      const words = preview.split(/\s+/);
      let currentLine = '';
      for (const word of words) {
        if ((currentLine + ' ' + word).length > maxPreview) {
          if (currentLine) previewLines.push(currentLine);
          currentLine = word;
          if (previewLines.length >= 4) break;
        } else {
          currentLine = currentLine ? currentLine + ' ' + word : word;
        }
      }
      if (currentLine && previewLines.length < 4) previewLines.push(currentLine);
      if (previewLines.length === 0) previewLines.push(trimmedPreview);
    } else {
      previewLines.push(trimmedPreview);
    }
  } else {
    previewLines.push('No preview available');
  }

  return h(
    Box,
    {flexDirection: 'column'},
    h(
      Text,
      {
        color: active ? 'white' : 'white',
        backgroundColor: active ? '#303030' : undefined,
      },
      `${active ? figures.pointer : ' '} ${header}`
    ),
    ...previewLines.map((line, i) =>
      h(Text, {key: i, dimColor: true}, '     ' + line)  // 5 spaces to align with agent name column
    )
  );
}

// Calculate actual line count for a session when expanded (1 header + N preview lines)
function calcExpandedLines(session, maxPreview) {
  const preview = session.preview || '';
  if (!preview) return 2; // header + "No preview available"
  const words = preview.split(/\s+/);
  let lineCount = 0;
  let currentLine = '';
  for (const word of words) {
    if ((currentLine + ' ' + word).length > maxPreview) {
      if (currentLine) lineCount++;
      currentLine = word;
      if (lineCount >= 4) break; // max 4 preview lines
    } else {
      currentLine = currentLine ? currentLine + ' ' + word : word;
    }
  }
  if (currentLine && lineCount < 4) lineCount++;
  return 1 + Math.max(1, lineCount); // header + at least 1 preview line
}

function ResultsView({onSelect, onQuit, clearScreen = () => {}, focusIndex = 0, onChangeIndex}) {
  const clampIndex = React.useCallback(
    (i) => Math.max(0, Math.min(i, sessions.length - 1)),
    [sessions.length]
  );
  const initialIndex = clampIndex(focusIndex);
  const [index, setIndex] = useState(initialIndex);
  const [scroll, setScroll] = useState(0);
  const [numBuffer, setNumBuffer] = useState('');
  const [expanded, setExpanded] = useState({});  // { [session_id]: true | false }
  const [zoomAll, setZoomAll] = useState(startZoomed);  // global zoom state
  const [resetting, setResetting] = useState(false);  // "blink" state for clean re-render

  const {stdout} = useStdout();
  const width = stdout?.columns || 80;
  const height = stdout?.rows || 24;
  const headerRows = 3; // title + blank + maybe kw
  const footerRows = 2; // instruction line + spacing
  const availableRows = Math.max(1, height - headerRows - footerRows);
  const maxPreview = Math.max(0, width - 30);

  // Pre-compute actual expanded line counts for all sessions (memoized)
  const expandedLineCounts = React.useMemo(
    () => sessions.map(s => calcExpandedLines(s, maxPreview)),
    [maxPreview]
  );

  // In normal mode: fixed 2 lines per item
  // In zoom mode: use actual line counts, computed dynamically from scroll position
  const normalMaxItems = Math.max(1, Math.floor(availableRows / 2));

  // Sync with parent's focusIndex when it changes
  React.useEffect(() => {
    const next = clampIndex(focusIndex);
    if (next !== index) {
      setIndex(next);
      const maxScroll = Math.max(0, sessions.length - normalMaxItems);
      let nextScroll = scroll;
      if (next < nextScroll) nextScroll = next;
      else if (next >= nextScroll + normalMaxItems) nextScroll = next - normalMaxItems + 1;
      nextScroll = Math.max(0, Math.min(nextScroll, maxScroll));
      setScroll(nextScroll);
    }
  }, [focusIndex, clampIndex, index, normalMaxItems, scroll, sessions.length]);

  // Compute visible rows, effective maxItems, and clampedScroll based on zoom state
  const {visible, maxItems, clampedScroll} = React.useMemo(() => {
    if (!zoomAll) {
      // Normal mode: fixed height per row
      const clamped = Math.max(0, Math.min(scroll, Math.max(0, sessions.length - normalMaxItems)));
      return {
        visible: sessions.slice(clamped, clamped + normalMaxItems),
        maxItems: normalMaxItems,
        clampedScroll: clamped
      };
    }
    // Zoom mode: greedily fill viewport based on actual heights
    let totalLines = 0;
    let count = 0;
    const clamped = Math.max(0, Math.min(scroll, sessions.length - 1));
    for (let i = clamped; i < sessions.length; i++) {
      const rowLines = expandedLineCounts[i];
      if (totalLines + rowLines > availableRows && count > 0) break;
      totalLines += rowLines;
      count++;
    }
    return {
      visible: sessions.slice(clamped, clamped + count),
      maxItems: Math.max(1, count),
      clampedScroll: clamped
    };
  }, [zoomAll, scroll, normalMaxItems, availableRows, expandedLineCounts]);

  // Ensure scroll starts at top, unless focused row is below viewport
  // Use normalMaxItems (not dynamic maxItems) to avoid dependency cycle
  React.useEffect(() => {
    const top = 0;
    let nextScroll = top;
    if (initialIndex >= normalMaxItems) {
      nextScroll = Math.max(0, initialIndex - 1);
    }
    setScroll(nextScroll);
    setNumBuffer('');
  }, [initialIndex, normalMaxItems]);

  // "Blink" effect: after rendering minimal content, immediately render actual content
  // This forces Ink to do a clean re-render instead of confused incremental update
  React.useEffect(() => {
    if (resetting) {
      const timer = setTimeout(() => setResetting(false), 0);
      return () => clearTimeout(timer);
    }
  }, [resetting]);

  // Helper to notify parent after state updates (deferred to avoid React warnings)
  const notifyParent = React.useCallback((idx) => {
    if (onChangeIndex) {
      setTimeout(() => onChangeIndex(idx), 0);
    }
  }, [onChangeIndex]);

  useInput((input, key) => {
    if (key.escape) {
      // If typing a number, clear the buffer instead of quitting
      if (numBuffer) {
        setNumBuffer('');
        return;
      }
      return onQuit();
    }
    if (key.return) {
      if (numBuffer) {
        const target = Math.min(
          Math.max(parseInt(numBuffer, 10) - 1, 0),
          sessions.length - 1,
        );
        setIndex(target);
        setScroll((prev) => {
          if (target < prev) return target;
          if (target >= prev + maxItems) return target - maxItems + 1;
          return prev;
        });
        notifyParent(target);
        setNumBuffer('');
        return;
      }
      clearScreen();
      notifyParent(index);
      return onSelect(index);
    }
    if (key.upArrow || input === 'k') {
      setIndex((i) => {
        const next = Math.max(0, i - 1);
        setScroll((prev) => (next < prev ? next : prev));
        setNumBuffer('');
        notifyParent(next);
        return next;
      });
    }
    if (key.downArrow || input === 'j') {
      setIndex((i) => {
        const next = Math.min(sessions.length - 1, i + 1);
        setScroll((prev) => (next >= prev + maxItems ? prev + 1 : prev));
        setNumBuffer('');
        notifyParent(next);
        return next;
      });
    }
    // Page up with 'u' - move by maxItems
    if (input === 'u') {
      setIndex((i) => {
        const next = Math.max(0, i - maxItems);
        // Adjust scroll: if new index is above viewport, scroll to show it
        setScroll((prev) => (next < prev ? next : prev));
        setNumBuffer('');
        notifyParent(next);
        return next;
      });
      return;
    }
    // Page down with 'd' - move by maxItems
    if (input === 'd') {
      setIndex((i) => {
        const next = Math.min(sessions.length - 1, i + maxItems);
        // Adjust scroll: if new index is below viewport, scroll to show it
        setScroll((prev) => {
          if (next >= prev + maxItems) {
            return Math.min(next, Math.max(0, sessions.length - maxItems));
          }
          return prev;
        });
        setNumBuffer('');
        notifyParent(next);
        return next;
      });
      return;
    }
    // SPACE toggles expansion for the currently selected row
    if (input === ' ') {
      const row = sessions[index];
      setExpanded(prev => ({
        ...prev,
        [row.session_id]: !prev[row.session_id]
      }));
      return;
    }
    // 'z' toggles zoom mode (expand all rows)
    // "Blink" approach: clearScreen + render minimal content first,
    // then useEffect triggers second render with actual content.
    // clearScreen() alone corrupted Ink's state; blink alone didn't clear old content.
    // Combined: clearScreen clears terminal, minimal render resets Ink's state,
    // then actual content renders fresh.
    if (input === 'z' || input === 'Z') {
      clearScreen();
      setResetting(true);
      setIndex(0);
      setScroll(0);
      setExpanded({});  // clear individual expansions
      setZoomAll(prev => !prev);
      return;
    }
    const num = Number(input);
    if (!Number.isNaN(num) && num >= 0 && num <= 9) {
      // For small selection screens (resultsTitle set), direct number selection
      if (resultsTitle && num >= 1 && num <= sessions.length) {
        clearScreen();
        return onSelect(num - 1);
      }
      const candidate = (numBuffer + input).replace(/^0+/, '') || '0';
      setNumBuffer(candidate);
    }
  });

  // "Blink" render: show minimal content to reset Ink's state, then actual content renders next tick
  if (resetting) {
    return h(Box, {flexDirection: 'column'}, h(Text, null, ' '));
  }

  const kw = keywords.join(', ');
  const kwTrim = kw.length > width - 15 ? kw.slice(0, width - 18) + '…' : kw;
  // visible and clampedScroll now computed in useMemo above

  const pad = (() => {
    const proj = Math.max(...sessions.map((s) => (s.project || '').length), 0);
    const branch = Math.max(
      ...sessions.map((s) => {
        const raw = s.branch ? `${BRANCH_ICON} ${s.branch}` : '';
        return raw.length;
      }),
      0,
    );
    const lines = Math.max(...sessions.map((s) => (formatLines(s.lines) || '').length), 0);
    const date = Math.max(
      ...sessions.map((s) => (formatDateRange(s.create_time, s.mod_time) || '').length),
      0,
    );
    const agentId = Math.max(
      ...sessions.map((s) => {
        const anno = toAnno(s);
        const id = (s.session_id || '').slice(0, 8);
        return `[${s.agent_display}] ${id}${anno ? ' ' + anno : ''}`.length;
      }),
      0,
    );
    return {project: proj, branch, lines, date, agentId};
  })();

  const annoSet = new Set();
  sessions.forEach((s) => {
    const a = toAnno(s);
    if (a.includes('t')) annoSet.add('t');
    if (a.includes('r')) annoSet.add('r');
    if (a.includes('sub')) annoSet.add('sub');
  });
  // Legend for annotation symbols - (r) is "rolled-over" (internally "continued")
  const annoLine = (() => {
    if (!annoSet.size) return null;
    const parts = [];
    if (annoSet.has('t')) parts.push('(t)=trimmed');
    if (annoSet.has('r')) parts.push('(r)=rolled-over');
    if (annoSet.has('sub')) parts.push('(sub)=sub-agent (not resumable)');
    return parts.join(' ');
  })();

  return h(
    Box,
    {flexDirection: 'column'},
    h(
      Box,
      {marginBottom: 0},
      h(Text, null, chalk.bold.cyan(resultsTitle || ' Sessions '), resultsTitle ? '' : ' ', resultsTitle ? '' : chalk.dim(kwTrim))
    ),
    scopeLine && resultsTitle
      ? h(Box, {marginBottom: 1}, h(Text, {dimColor: true}, scopeLine))
      : null,
    !resultsTitle ? h(Box, {marginBottom: 1}) : null,
    h(
      Box,
      // React key changes with zoomAll to force full remount, avoiding Ink render artifacts
      {key: zoomAll ? 'zoomed' : 'normal', flexDirection: 'column', gap: 0},
      visible.map((s, i) =>
        h(SessionRow, {
          key: s.session_id,
          session: s,
          active: clampedScroll + i === index,
          index: clampedScroll + i,
          width,
          pad,
          isExpanded: zoomAll || !!expanded[s.session_id],
        })
      )
    ),
    h(
      Box,
      {marginTop: 1},
      h(
        Text,
        {dimColor: true},
        (() => {
          // Minimal help for custom title screens (like resume selection)
          if (resultsTitle) {
            const nums = sessions.length === 1 ? '1' : '1/2';
            return `${nums}: select  Enter: select  Esc: quit  ↑/↓: move`;
          }
          const currentRowExpanded = zoomAll || !!expanded[sessions[index]?.session_id];
          const spaceLabel = currentRowExpanded ? 'Space: collapse row' : 'Space: expand row';
          const zoomLabel = zoomAll ? 'z: unzoom' : 'z: zoom all';
          const numLabel = numBuffer ? ` [typing ${numBuffer}]` : '';
          return `Enter: select  Esc: quit  ↑/↓/j/k: move  u/d: page  ${spaceLabel}  ${zoomLabel}  num+Enter: jump${numLabel}`;
        })()
      )
    ),
    // scopeLine shown at top when resultsTitle is set, otherwise at bottom
    scopeLine && !resultsTitle
      ? h(
          Box,
          {marginTop: 0},
          h(Text, {dimColor: true}, scopeLine)
        )
      : null,
    tipLine
      ? h(
          Box,
          {marginTop: 0},
          h(Text, {dimColor: true}, tipLine)
        )
      : null,
    annoLine
      ? h(
          Box,
          {marginTop: 0},
          h(Text, {dimColor: true}, annoLine)
        )
      : null
  );
}

function ConfirmView({session, actionLabel, onConfirm, onBack}) {
  useInput((input, key) => {
    if (key.escape) return onBack();
    if (key.return) return onConfirm();
  });
  const branchDisplay = session.branch ? `${BRANCH_ICON} ${session.branch}` : '';

  return h(
    Box,
    {flexDirection: 'column'},
    h(Text, null,
      chalk.bgBlue.black(' Confirm action '), ' ', actionLabel, ' ',
      colorize.project(session.project || ''), ' ',
      colorize.branch(branchDisplay)
    ),
    h(Text, null, ...formatSessionHeader(session)),
    h(Box, {marginBottom: 1}, renderPreview(session.preview) || null),
    h(Text, {dimColor: true}, 'Enter: run action & exit  Esc: back')
  );
}

function ActionView({session, onBack, onDone, clearScreen}) {
  const baseItems = filteredActions(session.is_sidechain);
  const [index, setIndex] = useState(0);

  const items = baseItems.map((item, idx) => ({
    ...item,
    label: `${idx + 1}. ${item.label}`,
    number: idx + 1,
  }));

  useInput((input, key) => {
    if (key.escape) {
      clearScreen();
      onBack();
    }
    if (key.return) {
      onDone(items[index].value);
      return;
    }
    if (key.upArrow || input === 'k') {
      setIndex((i) => (i === 0 ? items.length - 1 : i - 1));
    }
    if (key.downArrow || input === 'j') {
      setIndex((i) => (i === items.length - 1 ? 0 : i + 1));
    }
    const num = Number(input);
    if (!Number.isNaN(num) && num >= 1 && num <= items.length) {
      setIndex(num - 1);
    }
  });

  const fullId = session.session_id || '';  // Show full session ID in actions view
  const branchDisplay = session.branch ? `${BRANCH_ICON} ${session.branch}` : '';

  return h(
    Box,
    {flexDirection: 'column'},
    h(Text, null, ''),
    h(
      Box,
      {flexDirection: 'column'},
      h(Text, null,
        chalk.bgCyan.black(' Actions '), ' ',
        colorize.project(session.project || ''), ' ',
        colorize.branch(branchDisplay)
      ),
      h(Text, null, ...formatSessionHeader(session, fullId)),
      renderPreview(session.preview),
      // Warning for sub-agent sessions
      session.is_sidechain ? h(
        Text,
        {color: 'yellow', marginTop: 1},
        '⚠ Sub-agent session (not resumable)'
      ) : null
    ),
    h(Box, {marginBottom: 1}),
    h(Box, {flexDirection: 'column'},
      ...items.map((item, idx) => {
        const isHighlighted = idx === index;
        return h(
          Text,
          {
            key: item.value,
            color: isHighlighted ? 'blue' : 'white',
          },
          `${isHighlighted ? figures.pointer : ' '} ${item.label}`
        );
      })
    ),
    h(
      Box,
      {marginTop: 1},
      h(Text, {dimColor: true}, 'Enter: select  Esc: back  ↑/↓: move  number: jump')
    )
  );
}

function ResumeView({onBack, onDone, session, clearScreen}) {
  const [index, setIndex] = useState(0);
  const items = RESUME_SUBMENU.map((opt, idx) => ({...opt, label: `${idx + 1}. ${opt.label}`}));

  useInput((input, key) => {
    if (key.escape) {
      clearScreen();
      onBack();
    }
    if (key.return) {
      onDone(items[index].value);
      return;
    }
    if (key.upArrow || input === 'k') {
      setIndex((i) => (i === 0 ? items.length - 1 : i - 1));
    }
    if (key.downArrow || input === 'j') {
      setIndex((i) => (i === items.length - 1 ? 0 : i + 1));
    }
    const num = Number(input);
    if (!Number.isNaN(num) && num >= 1 && num <= items.length) {
      setIndex(num - 1);
    }
  });

  const branchDisplay = session.branch ? `${BRANCH_ICON} ${session.branch}` : '';

  return h(
    Box,
    {flexDirection: 'column'},
    h(
      Box,
      {flexDirection: 'column'},
      h(Text, null,
        chalk.bgGreen.black(' Resume/Trim '), ' ',
        colorize.project(session.project || ''), ' ',
        colorize.branch(branchDisplay)
      ),
      h(Text, null, ...formatSessionHeader(session)),
      renderPreview(session.preview)
    ),
    h(Box, {marginBottom: 1}),
    h(Box, {flexDirection: 'column'},
      ...items.map((item, idx) => {
        const isHighlighted = idx === index;
        return h(
          Text,
          {
            key: item.value,
            color: isHighlighted ? 'blue' : 'white',
          },
          `${isHighlighted ? figures.pointer : ' '} ${item.label}`
        );
      })
    ),
    h(
      Box,
      {marginTop: 1},
      h(Text, {dimColor: true}, 'Enter: select  Esc: back  ↑/↓: move  number: jump')
    )
  );
}

function TrimView({onBack, onDone, session, clearScreen}) {
  const [index, setIndex] = useState(0);
  const items = TRIM_SUBMENU.map((opt, idx) => ({...opt, label: `${idx + 1}. ${opt.label}`}));

  useInput((input, key) => {
    if (key.escape) {
      clearScreen();
      onBack();
    }
    if (key.return) {
      onDone(items[index].value);
      return;
    }
    if (key.upArrow || input === 'k') {
      setIndex((i) => (i === 0 ? items.length - 1 : i - 1));
    }
    if (key.downArrow || input === 'j') {
      setIndex((i) => (i === items.length - 1 ? 0 : i + 1));
    }
    const num = Number(input);
    if (!Number.isNaN(num) && num >= 1 && num <= items.length) {
      setIndex(num - 1);
    }
  });

  const branchDisplay = session.branch ? `${BRANCH_ICON} ${session.branch}` : '';

  return h(
    Box,
    {flexDirection: 'column'},
    h(
      Box,
      {flexDirection: 'column'},
      h(Text, null,
        chalk.bgYellow.black(' Trim Session '), ' ',
        colorize.project(session.project || ''), ' ',
        colorize.branch(branchDisplay)
      ),
      h(Text, null, ...formatSessionHeader(session)),
      renderPreview(session.preview)
    ),
    h(Box, {marginBottom: 1}),
    h(Box, {flexDirection: 'column'},
      ...items.map((item, idx) => {
        const isHighlighted = idx === index;
        return h(
          Text,
          {
            key: item.value,
            color: isHighlighted ? 'blue' : 'white',
          },
          `${isHighlighted ? figures.pointer : ' '} ${item.label}`
        );
      })
    ),
    h(
      Box,
      {marginTop: 1},
      h(Text, {dimColor: true}, 'Enter: select  Esc: back  ↑/↓: move  number: jump')
    )
  );
}

/**
 * TrimConfirmView - Confirmation dialog after trim creates a new session file.
 * Shows two options: Resume (default) or Delete & Exit.
 * Escape cancels without deleting (file remains).
 *
 * When nothing_to_trim is true, shows simpler UI for resuming original session.
 */
function TrimConfirmView({onDone, onCancel, clearScreen, trimInfo}) {
  const [index, setIndex] = useState(0);

  // Check if this is the "nothing to trim" case
  const nothingToTrim = trimInfo.nothing_to_trim || false;

  const items = nothingToTrim
    ? [
        {value: 'resume', label: 'Resume original session'},
        {value: 'back', label: 'Back to menu'},
      ]
    : [
        {value: 'resume', label: 'Resume trimmed session'},
        {value: 'delete', label: 'Delete session file & exit'},
      ];

  useInput((input, key) => {
    if (key.escape) {
      clearScreen();
      onCancel();
    }
    if (key.return) {
      clearScreen();
      onDone(items[index].value);
      return;
    }
    if (key.upArrow || input === 'k') {
      setIndex((i) => (i === 0 ? items.length - 1 : i - 1));
    }
    if (key.downArrow || input === 'j') {
      setIndex((i) => (i === items.length - 1 ? 0 : i + 1));
    }
    const num = Number(input);
    if (!Number.isNaN(num) && num >= 1 && num <= items.length) {
      setIndex(num - 1);
    }
  });

  // Extract info from trimInfo
  const sessionId = (trimInfo.new_session_id || trimInfo.original_session_id || '').slice(0, 12);
  const linesTrimmed = trimInfo.lines_trimmed || 0;
  const tokensSaved = trimInfo.tokens_saved || 0;
  const outputFile = trimInfo.output_file || '';

  // Different header based on whether trimming happened
  const header = nothingToTrim
    ? h(Text, null, chalk.bgYellow.black(' Nothing to Trim '))
    : h(Text, null, chalk.bgGreen.black(' Trim Complete '));

  const infoLines = nothingToTrim
    ? [
        h(Text, null, ''),
        h(Text, null, chalk.yellow('✓ '), 'Session is already well-optimized'),
        h(Text, null, chalk.dim('   No changes were made')),
      ]
    : [
        h(Text, null, ''),
        h(Text, null, chalk.green('✓ '), 'New session: ', chalk.cyan(sessionId), '...'),
        h(Text, null, chalk.green('✓ '), 'Lines trimmed: ', chalk.yellow(String(linesTrimmed))),
        h(Text, null, chalk.green('✓ '), 'Tokens saved: ', chalk.yellow(`~${tokensSaved.toLocaleString()}`)),
        outputFile ? h(Text, {dimColor: true}, `   ${outputFile}`) : null,
      ];

  const footerText = nothingToTrim
    ? 'Enter: select  Esc: back  ↑/↓: move'
    : 'Enter: select  Esc: cancel (keep file)  ↑/↓: move';

  return h(
    Box,
    {flexDirection: 'column'},
    h(
      Box,
      {flexDirection: 'column', marginBottom: 1},
      header,
      ...infoLines.filter(Boolean)
    ),
    h(Box, {flexDirection: 'column'},
      ...items.map((item, idx) => {
        const isHighlighted = idx === index;
        return h(
          Text,
          {
            key: item.value,
            color: isHighlighted ? 'blue' : 'white',
          },
          `${isHighlighted ? figures.pointer : ' '} ${idx + 1}. ${item.label}`
        );
      })
    ),
    h(
      Box,
      {marginTop: 1},
      h(Text, {dimColor: true}, footerText)
    )
  );
}

/**
 * DirConfirmView - Confirmation dialog when session is from a different directory.
 * Shows current dir vs session dir with Yes/No options.
 * Escape cancels and goes back.
 */
function DirConfirmView({onDone, onCancel, clearScreen, dirInfo}) {
  const [index, setIndex] = useState(0);

  const items = [
    {value: 'yes', label: 'Yes, change directory and proceed'},
    {value: 'no', label: 'No, proceed without changing directory'},
  ];

  useInput((input, key) => {
    if (key.escape) {
      clearScreen();
      onCancel();
    }
    if (key.return) {
      clearScreen();
      onDone(items[index].value);
      return;
    }
    if (key.upArrow || input === 'k') {
      setIndex((i) => (i === 0 ? items.length - 1 : i - 1));
    }
    if (key.downArrow || input === 'j') {
      setIndex((i) => (i === items.length - 1 ? 0 : i + 1));
    }
    const num = Number(input);
    if (!Number.isNaN(num) && num >= 1 && num <= items.length) {
      setIndex(num - 1);
    }
  });

  const currentDir = dirInfo.current_dir || '';
  const sessionDir = dirInfo.session_dir || '';

  return h(
    Box,
    {flexDirection: 'column'},
    h(
      Box,
      {flexDirection: 'column', marginBottom: 1},
      h(Text, null, chalk.bgYellow.black(' Different Directory ')),
      h(Text, null, ''),
      h(Text, null, 'This session is from a different project directory:'),
      h(Text, null, ''),
      h(Text, null, '  Current:  ', chalk.dim(currentDir)),
      h(Text, null, '  Session:  ', chalk.cyan(sessionDir)),
      h(Text, null, ''),
      h(Text, null, 'Change to the session\'s directory?')
    ),
    h(Box, {flexDirection: 'column'},
      ...items.map((item, idx) => {
        const isHighlighted = idx === index;
        return h(
          Text,
          {
            key: item.value,
            color: isHighlighted ? 'blue' : 'white',
          },
          `${isHighlighted ? figures.pointer : ' '} ${idx + 1}. ${item.label}`
        );
      })
    ),
    h(
      Box,
      {marginTop: 1},
      h(Text, {dimColor: true}, 'Enter: select  Esc: cancel  ↑/↓: move')
    )
  );
}

function TrimForm({onSubmit, onBack, clearScreen, session}) {
  const [field, setField] = useState('tools');
  const [tools, setTools] = useState('');
  const [threshold, setThreshold] = useState('500');
  const [assistant, setAssistant] = useState('');

  useInput((input, key) => {
    if (key.escape) {
      clearScreen();
      return onBack();
    }
    // Down arrow: cycle through fields (wrap around)
    if (key.downArrow) {
      if (field === 'tools') setField('threshold');
      else if (field === 'threshold') setField('assistant');
      else if (field === 'assistant') setField('tools'); // Cycle back to top
      return;
    }
    // Up arrow: cycle through fields (wrap around)
    if (key.upArrow) {
      if (field === 'threshold') setField('tools');
      else if (field === 'assistant') setField('threshold');
      else if (field === 'tools') setField('assistant'); // Cycle to bottom
      return;
    }
    // Enter: advance to next field, or submit on last field
    if (key.return) {
      if (field === 'tools') setField('threshold');
      else if (field === 'threshold') setField('assistant');
      else if (field === 'assistant') {
        onSubmit({
          tools: tools || null,
          threshold: Number(threshold) || 500,
          trim_assistant: assistant ? Number(assistant) : null,
        });
      }
      return;
    }
    if (key.backspace || key.delete) {
      if (field === 'tools') setTools((t) => t.slice(0, -1));
      if (field === 'threshold') setThreshold((t) => t.slice(0, -1));
      if (field === 'assistant') setAssistant((t) => t.slice(0, -1));
      return;
    }
    if (input) {
      if (field === 'tools') setTools((t) => t + input);
      if (field === 'threshold') setThreshold((t) => t + input);
      if (field === 'assistant') setAssistant((t) => t + input);
    }
  });

  const arrow = figures.pointer;
  const branchDisplay = session.branch ? `${BRANCH_ICON} ${session.branch}` : '';

  return h(
    Box,
    {flexDirection: 'column'},
    h(
      Box,
      {flexDirection: 'column'},
      h(Text, null,
        chalk.bgYellow.black(' Trim options '), ' ',
        colorize.project(session.project || ''), ' ',
        colorize.branch(branchDisplay)
      ),
      h(Text, null, ...formatSessionHeader(session)),
      renderPreview(session.preview)
    ),
    h(Box, {marginBottom: 1}),
    h(Text, {dimColor: true}, '↑↓: cycle fields | Enter: next/submit | Esc: back'),
    h(Box, {marginBottom: 1}),
    // Tools field
    h(
      Box,
      {flexDirection: 'column'},
      h(
        Text,
        null,
        field === 'tools' ? chalk.cyan(arrow) : ' ',
        ' Tools to trim ',
        h(Text, {dimColor: true}, "(comma-separated, e.g., 'bash,read,edit')")
      ),
      h(
        Text,
        null,
        '  > ',
        h(Text, {color: field === 'tools' ? 'yellow' : undefined}, tools || chalk.dim('(all tools)'))
      )
    ),
    h(Text, null, ''),
    // Threshold field
    h(
      Box,
      {flexDirection: 'column'},
      h(
        Text,
        null,
        field === 'threshold' ? chalk.cyan(arrow) : ' ',
        ' Length threshold in characters ',
        h(Text, {dimColor: true}, '(tool results longer than this get trimmed)')
      ),
      h(
        Text,
        null,
        '  > ',
        h(Text, {color: field === 'threshold' ? 'yellow' : undefined}, threshold || '500')
      )
    ),
    h(Text, null, ''),
    // Assistant messages field
    h(
      Box,
      {flexDirection: 'column'},
      h(
        Text,
        null,
        field === 'assistant' ? chalk.cyan(arrow) : ' ',
        ' Trim assistant messages ',
        h(Text, {dimColor: true}, '(optional)')
      ),
      h(Text, {dimColor: true}, '    Positive (e.g., 10): Trim first N messages exceeding threshold'),
      h(Text, {dimColor: true}, '    Negative (e.g., -5): Keep only last N messages'),
      h(Text, {dimColor: true}, '    Blank: Skip (no assistant message trimming)'),
      h(
        Text,
        null,
        '  > ',
        h(Text, {color: field === 'assistant' ? 'yellow' : undefined}, assistant || chalk.dim('(skip)'))
      )
    )
  );
}

function LineageView({session, rpcPath, onContinue, onBack, clearScreen}) {
  const [stage, setStage] = useState('loading');
  const [lineage, setLineage] = useState([]);
  const [error, setError] = useState('');
  const startedRef = React.useRef(false);

  React.useEffect(() => {
    if (stage === 'loading' && !startedRef.current) {
      startedRef.current = true;
      if (!rpcPath) {
        setError('RPC path missing');
        setStage('done');
        return;
      }
      const req = {
        action: 'lineage',
        agent: session.agent,
        session_id: session.session_id,
        file_path: session.file_path,
        cwd: session.cwd,
        claude_home: session.claude_home,
      };
      const proc = spawnSync('python3', [rpcPath], {
        input: JSON.stringify(req),
        encoding: 'utf8',
      });
      if (proc.error) {
        setError(proc.error.message);
        setStage('done');
        return;
      }
      try {
        const out = JSON.parse(proc.stdout || '{}');
        if (out.status === 'ok' && out.lineage) {
          setLineage(out.lineage);
        }
      } catch (e) {
        // Ignore parse errors, just show empty lineage
      }
      setStage('done');
    }
  }, [stage, rpcPath, session]);

  useInput((input, key) => {
    if (stage !== 'done') return;
    if (key.escape) {
      clearScreen();
      return onBack();
    }
    if (key.return) {
      clearScreen();
      return onContinue();
    }
  });

  const branchDisplay = session.branch ? `${BRANCH_ICON} ${session.branch}` : '';

  return h(
    Box,
    {flexDirection: 'column'},
    h(
      Box,
      {flexDirection: 'column'},
      h(Text, null,
        chalk.bgYellow.black(' Session Lineage '), ' ',
        colorize.project(session.project || ''), ' ',
        colorize.branch(branchDisplay)
      ),
      h(Text, null, ...formatSessionHeader(session))
    ),
    h(Box, {marginTop: 1}),
    stage === 'loading'
      ? h(Text, {dimColor: true}, 'Loading session lineage...')
      : error
        ? h(Text, {color: 'red'}, error)
        : h(
            Box,
            {flexDirection: 'column'},
            lineage.length === 0
              ? h(Text, {dimColor: true}, 'This is the original session (no continuation history)')
              : h(
                  Box,
                  {flexDirection: 'column'},
                  h(Text, null, chalk.cyan(`Found ${lineage.length} session(s) in continuation chain:`)),
                  h(Box, {marginTop: 1}),
                  ...lineage.map((node, i) =>
                    h(
                      Box,
                      {key: i, flexDirection: 'column'},
                      h(Text, null,
                        `  ${i + 1}. `,
                        chalk.white(node.session_file),
                        node.derivation_type ? chalk.dim(` (${node.derivation_type})`) : ''
                      )
                    )
                  )
                )
          ),
    h(Box, {marginTop: 1}),
    stage === 'done'
      ? h(Text, {dimColor: true}, 'Enter: continue to options  Esc: back')
      : null
  );
}

function ContinueForm({onSubmit, onBack, clearScreen, session}) {
  const [field, setField] = useState('agent');
  const [agent, setAgent] = useState(session.agent || 'claude');
  const [rolloverType, setRolloverType] = useState('quick'); // 'quick' or 'context'
  const [prompt, setPrompt] = useState('');

  // Fields order: agent -> rollover_type -> prompt (only if context recovery)
  const getNextField = (current) => {
    if (current === 'agent') return 'rollover_type';
    if (current === 'rollover_type') return rolloverType === 'context' ? 'prompt' : null;
    if (current === 'prompt') return null;
    return null;
  };
  const getPrevField = (current) => {
    if (current === 'prompt') return 'rollover_type';
    if (current === 'rollover_type') return 'agent';
    if (current === 'agent') return rolloverType === 'context' ? 'prompt' : 'rollover_type';
    return null;
  };

  useInput((input, key) => {
    if (key.escape) {
      clearScreen();
      return onBack();
    }
    // Down arrow: cycle through fields (wrap around)
    if (key.downArrow) {
      const next = getNextField(field);
      if (next) setField(next);
      else setField('agent'); // Wrap to top
      return;
    }
    // Up arrow: cycle through fields (wrap around)
    if (key.upArrow) {
      const prev = getPrevField(field);
      if (prev) setField(prev);
      return;
    }
    // Enter: advance to next field, or submit on last field
    if (key.return) {
      const next = getNextField(field);
      if (next) {
        setField(next);
      } else {
        // Submit - for quick resume, prompt is ignored
        onSubmit({agent, rollover_type: rolloverType, prompt: rolloverType === 'context' ? prompt : ''});
      }
      return;
    }
    if (key.backspace || key.delete) {
      if (field === 'agent' || field === 'rollover_type') return; // Can't backspace on selection
      if (field === 'prompt') setPrompt((t) => t.slice(0, -1));
      return;
    }
    if (input) {
      if (field === 'agent') {
        if (input === '1') setAgent('claude');
        else if (input === '2') setAgent('codex');
      } else if (field === 'rollover_type') {
        // Handle rollover type changes
        if (input === '1') {
          setRolloverType('quick');
        } else if (input === '2') {
          setRolloverType('context');
        }
      } else if (field === 'prompt') {
        setPrompt((t) => t + input);
      }
    }
  });

  const arrow = figures.pointer;
  const branchDisplay = session.branch ? `${BRANCH_ICON} ${session.branch}` : '';

  // Build the value display for each field
  const agentValue = agent === 'claude' ? 'Claude' : 'Codex';
  const rolloverValue = rolloverType === 'quick' ? 'Quick' : 'Context';

  return h(
    Box,
    {flexDirection: 'column'},
    // Banner header explaining rollover
    h(
      Box,
      {flexDirection: 'column', marginBottom: 1},
      h(Text, null, chalk.bgCyan.black(' 🔄 ROLLOVER '), ' ', chalk.cyan('Continue work in a fresh session')),
      h(Text, {dimColor: true}, '─'.repeat(60))
    ),
    // Session info
    h(
      Box,
      {flexDirection: 'column'},
      h(Text, null,
        colorize.project(session.project || ''), ' ',
        colorize.branch(branchDisplay)
      ),
      h(Text, null, ...formatSessionHeader(session)),
      renderPreview(session.preview)
    ),
    h(Box, {marginBottom: 1}),
    h(Text, {dimColor: true}, 'Esc: back | ↑↓: navigate | 1/2: select | Enter: submit'),
    h(Box, {marginBottom: 1}),
    // Agent field - compact horizontal with [1]/[2] labels
    h(
      Text,
      null,
      field === 'agent' ? chalk.cyan(figures.pointer) : ' ',
      ' Agent: ',
      h(Text, {color: agent === 'claude' ? 'yellow' : 'white'}, agent === 'claude' ? '[1] Claude' : ' 1  Claude'),
      '   ',
      h(Text, {color: agent === 'codex' ? 'yellow' : 'white'}, agent === 'codex' ? '[2] Codex' : ' 2  Codex')
    ),
    h(Text, {dimColor: true}, '─'.repeat(60)),
    // Rollover type section header
    h(Box, {marginTop: 1}),
    h(Text, null, field === 'rollover_type' ? chalk.cyan(figures.pointer) : ' ', ' Rollover Type:'),
    h(Box, {marginTop: 1}),
    // Quick Resume option
    h(
      Box,
      {flexDirection: 'column', marginLeft: 3},
      h(
        Text,
        null,
        rolloverType === 'quick' ? chalk.yellow('[1] Quick Resume') : chalk.white(' 1  Quick Resume')
      ),
      h(Text, {dimColor: true}, '    Inject session lineage only, and quickly start new session.'),
      h(Text, {dimColor: true}, '    You provide context-extraction instructions once session resumes.')
    ),
    h(Box, {marginTop: 1}),
    // Context Recovery option
    h(
      Box,
      {flexDirection: 'column', marginLeft: 3},
      h(
        Text,
        null,
        rolloverType === 'context' ? chalk.yellow('[2] Resume with Context Recovery') : chalk.white(' 2  Resume with Context Recovery')
      ),
      h(Text, {dimColor: true}, '    Inject lineage + use headless/non-interactive agents to extract context.'),
      h(Text, {dimColor: true}, '    Slower, but resumes with full understanding of last task and overall work.')
    ),
    // Context instructions field - only shown when on prompt field (after pressing Enter/down)
    // This prevents layout shift from interfering with rollover type selection
    (rolloverType === 'context' && field === 'prompt') ? h(
      Box,
      {flexDirection: 'column', marginTop: 1},
      h(Text, {dimColor: true}, '─'.repeat(60)),
      h(Box, {marginTop: 1}),
      h(
        Text,
        null,
        chalk.cyan(figures.pointer),
        ' Custom context recovery instructions ',
        chalk.dim('(optional)')
      ),
      h(
        Text,
        null,
        '   ',
        h(Text, {color: 'yellow'}, prompt || chalk.dim('(default: recover context of last task)'))
      )
    ) : null
  );
}

// Smart Trim form - instructions for what to trim
function SmartTrimForm({onSubmit, onBack, clearScreen, session}) {
  const defaultPrompt = 'trim messages that are not relevant to the last task being worked on in this session';
  const [prompt, setPrompt] = useState('');
  const [hasTyped, setHasTyped] = useState(false);

  useInput((input, key) => {
    if (key.escape) {
      clearScreen();
      return onBack();
    }
    if (key.return) {
      // Submit custom prompt if typed, otherwise use default
      onSubmit({prompt: hasTyped ? prompt : defaultPrompt});
      return;
    }
    if (key.backspace || key.delete) {
      if (hasTyped) {
        setPrompt((t) => t.slice(0, -1));
      }
      return;
    }
    if (input) {
      if (!hasTyped) {
        // First character typed - start fresh, don't append to default
        setHasTyped(true);
        setPrompt(input);
      } else {
        setPrompt((t) => t + input);
      }
    }
  });

  const branchDisplay = session.branch ? `${BRANCH_ICON} ${session.branch}` : '';

  // Display: show user's input in yellow, or default in dim gray
  const displayPrompt = hasTyped
    ? (prompt || chalk.dim('(none)'))
    : chalk.gray(defaultPrompt);

  return h(
    Box,
    {flexDirection: 'column'},
    h(
      Box,
      {flexDirection: 'column'},
      h(Text, null,
        chalk.bgMagenta.black(' Smart Trim '), ' ',
        colorize.project(session.project || ''), ' ',
        colorize.branch(branchDisplay)
      ),
      h(Text, null, ...formatSessionHeader(session)),
      renderPreview(session.preview)
    ),
    h(Box, {marginBottom: 1}),
    h(Text, {dimColor: true}, 'Enter: submit | Esc: back'),
    h(Box, {marginBottom: 1}),
    h(
      Box,
      {flexDirection: 'column'},
      h(
        Text,
        null,
        chalk.cyan(figures.pointer),
        ' Trim instructions'
      ),
      h(Text, {dimColor: true}, '    Tell the agent what to look for when trimming'),
      h(
        Text,
        null,
        '  > ',
        hasTyped ? h(Text, {color: 'yellow'}, displayPrompt) : h(Text, null, displayPrompt)
      )
    )
  );
}

// Find options form - interactive menu for find command options
function FindOptionsForm({onSubmit, onCancel, initialOptions, variant}) {
  // variant: 'find' | 'find-claude' | 'find-codex'
  const showAgents = variant === 'find';
  const showNoSub = variant !== 'find-codex';

  // Define fields based on variant
  const allFields = [
    'keywords', 'global', 'num_matches',
    ...(showAgents ? ['agents'] : []),
    'original',
    ...(showNoSub ? ['no_sub'] : []),
    'no_trim', 'no_cont', 'min_lines', 'before', 'after'
  ];

  // Mode: 'action' (top menu) or 'edit' (form editing)
  const [mode, setMode] = useState('action');
  const [actionIdx, setActionIdx] = useState(0); // 0=Submit, 1=Edit
  const [fieldIdx, setFieldIdx] = useState(0);
  const field = allFields[fieldIdx];

  // Form state with initial values
  const [keywords, setKeywords] = useState(initialOptions.keywords || '');
  const [globalSearch, setGlobalSearch] = useState(initialOptions.global || false);
  const [numMatches, setNumMatches] = useState(String(initialOptions.num_matches || 10));
  const [agents, setAgents] = useState(initialOptions.agents || []);
  const [original, setOriginal] = useState(initialOptions.original || false);
  const [noSub, setNoSub] = useState(initialOptions.no_sub || false);
  const [noTrim, setNoTrim] = useState(initialOptions.no_trim || false);
  const [noCont, setNoCont] = useState(initialOptions.no_cont || false);
  const [minLines, setMinLines] = useState(String(initialOptions.min_lines || ''));
  const [before, setBefore] = useState(initialOptions.before || '');
  const [after, setAfter] = useState(initialOptions.after || '');

  const doSubmit = () => onSubmit({
    keywords: keywords || null,
    global: globalSearch,
    num_matches: parseInt(numMatches, 10) || 10,
    agents: agents.length > 0 ? agents : null,
    original,
    no_sub: noSub,
    no_trim: noTrim,
    no_cont: noCont,
    min_lines: minLines ? parseInt(minLines, 10) : null,
    before: before || null,
    after: after || null,
  });

  useInput((input, key) => {
    if (mode === 'action') {
      // Action menu mode
      if (key.escape) return onCancel();
      if (key.upArrow) { setActionIdx(0); return; }
      if (key.downArrow) { setActionIdx(1); return; }
      if (key.return) {
        if (actionIdx === 0) return doSubmit(); // Submit
        setMode('edit'); // Edit options
        return;
      }
    } else {
      // Edit mode
      if (key.escape) {
        setMode('action'); // Return to action menu
        return;
      }

      // Navigate between fields
      if (key.return || key.downArrow || (key.tab && !key.shift)) {
        if (fieldIdx === allFields.length - 1) {
          setMode('action'); // Done editing, return to action menu
        } else {
          setFieldIdx((i) => i + 1);
        }
        return;
      }
      if (key.upArrow || (key.tab && key.shift)) {
        if (fieldIdx === 0) {
          setMode('action'); // Go back to action menu
        } else {
          setFieldIdx((i) => i - 1);
        }
        return;
      }

      // Handle input based on field type
      const booleanFields = ['global', 'original', 'no_sub', 'no_trim', 'no_cont'];
      const textFields = ['keywords', 'num_matches', 'min_lines', 'before', 'after'];

      if (booleanFields.includes(field)) {
        if (input === ' ' || input === 'y' || input === 'n' || input === '1' || input === '0') {
          const newVal = input === ' ' ? undefined : (input === 'y' || input === '1');
          if (field === 'global') setGlobalSearch(newVal !== undefined ? newVal : !globalSearch);
          if (field === 'original') setOriginal(newVal !== undefined ? newVal : !original);
          if (field === 'no_sub') setNoSub(newVal !== undefined ? newVal : !noSub);
          if (field === 'no_trim') setNoTrim(newVal !== undefined ? newVal : !noTrim);
          if (field === 'no_cont') setNoCont(newVal !== undefined ? newVal : !noCont);
        }
      } else if (field === 'agents') {
        if (input === '1' || input.toLowerCase() === 'c') {
          setAgents((a) => a.includes('claude') ? a.filter(x => x !== 'claude') : [...a, 'claude']);
        }
        if (input === '2' || input.toLowerCase() === 'x') {
          setAgents((a) => a.includes('codex') ? a.filter(x => x !== 'codex') : [...a, 'codex']);
        }
      } else if (textFields.includes(field)) {
        if (key.backspace || key.delete) {
          if (field === 'keywords') setKeywords((t) => t.slice(0, -1));
          if (field === 'num_matches') setNumMatches((t) => t.slice(0, -1));
          if (field === 'min_lines') setMinLines((t) => t.slice(0, -1));
          if (field === 'before') setBefore((t) => t.slice(0, -1));
          if (field === 'after') setAfter((t) => t.slice(0, -1));
        } else if (input && !key.ctrl) {
          if (field === 'keywords') setKeywords((t) => t + input);
          if (field === 'num_matches') setNumMatches((t) => t + input);
          if (field === 'min_lines') setMinLines((t) => t + input);
          if (field === 'before') setBefore((t) => t + input);
          if (field === 'after') setAfter((t) => t + input);
        }
      }
    }
  });

  const arrow = figures.pointer;
  const check = figures.tick;
  const renderBool = (val) => val ? chalk.green(check + ' Yes') : chalk.dim('No');
  const renderText = (val, placeholder) => val ? chalk.yellow(val) : chalk.dim(placeholder);
  const inEdit = mode === 'edit';

  const variantLabel = variant === 'find' ? 'All Agents' :
                       variant === 'find-claude' ? 'Claude' : 'Codex';

  return h(
    Box,
    {flexDirection: 'column'},
    h(Text, null, chalk.inverse.bold(` Find Sessions (${variantLabel}) `)),

    // Action menu (top)
    h(Box, {marginTop: 1, marginBottom: 1, flexDirection: 'column'},
      h(Box, null,
        h(Text, null, !inEdit && actionIdx === 0 ? chalk.cyan(arrow) : ' ', ' '),
        h(Text, {color: !inEdit && actionIdx === 0 ? 'cyan' : 'white'}, 'Submit search')
      ),
      h(Box, null,
        h(Text, null, !inEdit && actionIdx === 1 ? chalk.cyan(arrow) : ' ', ' '),
        h(Text, {color: !inEdit && actionIdx === 1 ? 'cyan' : 'white'}, 'Edit options...')
      )
    ),

    // Separator
    h(Text, {dimColor: true}, '─'.repeat(50)),

    // Options form (bottom) - always visible
    h(Box, {marginTop: 1, flexDirection: 'column'},
      h(Text, {dimColor: !inEdit}, inEdit ? '↑/↓: navigate  Enter: next  Space/y/n: toggle  Esc: done' : 'Options:'),
      h(Box, {marginTop: 1}),

      // Keywords
      h(Box, null,
        h(Text, null, inEdit && field === 'keywords' ? chalk.cyan(arrow) : ' ', ' Keywords: '),
        h(Text, null, renderText(keywords, '(comma-separated, optional)'))
      ),

      // Global search
      h(Box, null,
        h(Text, null, inEdit && field === 'global' ? chalk.cyan(arrow) : ' ', ' Global search (-g): '),
        h(Text, null, renderBool(globalSearch))
      ),

      // Num matches
      h(Box, null,
        h(Text, null, inEdit && field === 'num_matches' ? chalk.cyan(arrow) : ' ', ' Max results (-n): '),
        h(Text, null, renderText(numMatches, '10'))
      ),

      // Agents (only for unified find)
      showAgents ? h(Box, null,
        h(Text, null, inEdit && field === 'agents' ? chalk.cyan(arrow) : ' ', ' Agents (1=claude, 2=codex): '),
        h(Text, null, agents.length > 0 ? chalk.yellow(agents.join(', ')) : chalk.dim('all'))
      ) : null,

      // Original only
      h(Box, null,
        h(Text, null, inEdit && field === 'original' ? chalk.cyan(arrow) : ' ', ' Original only (--original): '),
        h(Text, null, renderBool(original))
      ),

      // No sub-agent (not for codex)
      showNoSub ? h(Box, null,
        h(Text, null, inEdit && field === 'no_sub' ? chalk.cyan(arrow) : ' ', ' Exclude sub-agents (--no-sub): '),
        h(Text, null, renderBool(noSub))
      ) : null,

      // No trim
      h(Box, null,
        h(Text, null, inEdit && field === 'no_trim' ? chalk.cyan(arrow) : ' ', ' Exclude trimmed (--no-trim): '),
        h(Text, null, renderBool(noTrim))
      ),

      // No rollover (internally "no_cont")
      h(Box, null,
        h(Text, null, inEdit && field === 'no_cont' ? chalk.cyan(arrow) : ' ', ' Exclude rollover (--no-roll): '),
        h(Text, null, renderBool(noCont))
      ),

      // Min lines
      h(Box, null,
        h(Text, null, inEdit && field === 'min_lines' ? chalk.cyan(arrow) : ' ', ' Min lines (--min-lines): '),
        h(Text, null, renderText(minLines, '(no minimum)'))
      ),

      // Before
      h(Box, null,
        h(Text, null, inEdit && field === 'before' ? chalk.cyan(arrow) : ' ', ' Before (--before): '),
        h(Text, null, renderText(before, '(no limit)'))
      ),

      // After
      h(Box, null,
        h(Text, null, inEdit && field === 'after' ? chalk.cyan(arrow) : ' ', ' After (--after): '),
        h(Text, null, renderText(after, '(no limit)'))
      ),

      h(Box, {marginTop: 1},
        h(Text, {dimColor: true}, 'Timestamps: YYYYMMDD, MM/DD/YY, YYYY-MM-DD with optional T or space + HH:MM:SS')
      )
    )
  );
}

// Get default query from action config
const DEFAULT_QUERY = ACTIONS.find(a => a.value === 'query')?.defaultQuery || 'Summarize this session';

function QueryView({session, rpcPath, onBack, onExit, clearScreen, exitOnBack = false}) {
  const {exit} = useApp();
  // If exitOnBack is true, Esc also exits (for direct invocation from Rust search)
  const handleBack = exitOnBack ? () => { onExit(); exit({exitCode: 0}); } : onBack;
  const [query, setQuery] = useState('');
  const [hasTyped, setHasTyped] = useState(false); // Track if user started typing
  const [stage, setStage] = useState('prompt'); // 'prompt', 'running', 'result'
  const [result, setResult] = useState('');
  const [error, setError] = useState('');

  const runQuery = (queryText) => {
    if (!rpcPath) {
      setError('RPC path missing');
      setStage('result');
      return;
    }
    setStage('running');
    // Use setTimeout to let React render the "running" state before blocking
    setTimeout(() => {
      const req = {
        action: 'query',
        agent: session.agent,
        session_id: session.session_id,
        file_path: session.file_path,
        cwd: session.cwd,
        claude_home: session.claude_home,
        query: queryText,
      };
      const proc = spawnSync('python3', [rpcPath], {
        input: JSON.stringify(req),
        encoding: 'utf8',
        maxBuffer: 10 * 1024 * 1024, // 10MB for potentially long responses
      });
      if (proc.error) {
        setError(proc.error.message);
        setStage('result');
        return;
      }
      try {
        const out = JSON.parse(proc.stdout || '{}');
        if (out.status === 'ok') {
          setResult(out.message || 'No response');
        } else {
          setError(out.message || 'Error');
        }
      } catch (e) {
        setError(proc.stdout || 'Bad RPC output');
      }
      setStage('result');
    }, 50); // Small delay to allow render
  };

  useInput((input, key) => {
    if (stage === 'prompt') {
      if (key.escape) {
        clearScreen();
        return handleBack();
      }
      if (key.return) {
        // Use default if user hasn't typed anything
        const q = hasTyped ? query.trim() : DEFAULT_QUERY;
        if (!q) {
          setError('Query cannot be empty');
          setStage('result');
          return;
        }
        runQuery(q);
        return;
      }
      if (key.backspace || key.delete) {
        if (hasTyped) {
          setQuery((q) => q.slice(0, -1));
        }
        return;
      }
      if (input) {
        if (!hasTyped) {
          // First keystroke clears placeholder
          setHasTyped(true);
          setQuery(input);
        } else {
          setQuery((q) => q + input);
        }
      }
    } else if (stage === 'result') {
      if (key.escape || key.return) {
        clearScreen();
        return handleBack();
      }
    }
  });

  const id = (session.session_id || '').slice(0, 8);
  const anno = toAnno(session);
  const date = formatDateRange(session.create_time, session.mod_time);
  const branchDisplay = session.branch ? `${BRANCH_ICON} ${session.branch}` : '';

  if (stage === 'running') {
    const agentName = session.agent === 'codex' ? 'Codex' : 'Claude';
    return h(
      Box,
      {flexDirection: 'column'},
      h(Text, null,
        chalk.bgMagenta.black(` QUERY `), ' ',
        colorize.project(session.project || ''), ' ',
        colorize.branch(branchDisplay)
      ),
      h(Box, {flexDirection: 'column', marginTop: 1},
        h(Text, {color: 'yellow'}, `⏳ Querying session using ${agentName} in non-interactive mode...`),
        h(Text, {dimColor: true}, '   This may take 30-60 seconds as the agent analyzes the session.')
      )
    );
  }

  return h(
    Box,
    {flexDirection: 'column'},
    h(Text, null,
      chalk.bgMagenta.black(` QUERY `), ' ',
      colorize.project(session.project || ''), ' ',
      colorize.branch(branchDisplay)
    ),
    h(Text, null, ...formatSessionHeader(session)),
    stage === 'prompt'
      ? h(
          Box,
          {flexDirection: 'column', marginTop: 1},
          h(Text, null, 'Enter your question about this session (or Enter for default):'),
          h(Text, null, '>', ' ', hasTyped ? (query || chalk.dim('type query...')) : chalk.dim(DEFAULT_QUERY)),
          h(Text, {dimColor: true}, exitOnBack ? 'Enter: run query  Esc: back to search' : 'Enter: run query  Esc: back')
        )
      : h(
          Box,
          {flexDirection: 'column', marginTop: 1},
          error
            ? h(Text, {color: 'red'}, error)
            : h(
                Box,
                {flexDirection: 'column'},
                h(Text, {color: 'cyan', bold: true}, '─── Response ───'),
                h(Text, null, result)
              ),
          h(Text, {dimColor: true, marginTop: 1}, exitOnBack ? 'Enter/Esc: back to search' : 'Enter/Esc: back to menu')
        )
  );
}

function NonLaunchView({session, action, rpcPath, onBack, onExit, clearScreen, exitOnBack = false}) {
  const {exit} = useApp();
  const needsDest = action === 'copy' || action === 'export';
  // If exitOnBack is true, Esc also exits (for direct invocation from Rust search)
  const handleBack = exitOnBack ? () => { onExit(); exit({exitCode: 0}); } : onBack;
  const [dest, setDest] = useState('');
  const [stage, setStage] = useState(needsDest ? 'prompt' : 'running');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [resultPath, setResultPath] = useState('');
  const startedRef = React.useRef(false);

  const runRpc = (destArg) => {
    if (!rpcPath) {
      setError('RPC path missing');
      setStage('result');
      return;
    }
    const req = {
      action,
      agent: session.agent,
      session_id: session.session_id,
      file_path: session.file_path,
      cwd: session.cwd,
      claude_home: session.claude_home,
      dest: destArg,
    };
    const proc = spawnSync('python3', [rpcPath], {
      input: JSON.stringify(req),
      encoding: 'utf8',
    });
    if (proc.error) {
      setError(proc.error.message);
      setStage('result');
      return;
    }
    try {
      const out = JSON.parse(proc.stdout || '{}');
      if (out.status === 'ok') {
        const rawMsg = out.message || 'Done';
        setMessage(rawMsg);
        if (out.path) setResultPath(out.path);
      } else {
        setError(out.message || 'Error');
      }
    } catch (e) {
      setError(proc.stdout || 'Bad RPC output');
    }
    setStage('result');
  };

  React.useEffect(() => {
    if (!needsDest && stage === 'running' && !startedRef.current) {
      startedRef.current = true;
      runRpc('');
    }
  }, [needsDest, stage]);

  useInput((input, key) => {
    if (stage === 'prompt') {
      if (key.escape) {
        clearScreen();
        return handleBack();
      }
      if (key.return) {
        const submittedDest = dest.trim();
        const finalDest = submittedDest || (action === 'export' ? session.default_export_path : '');
        if (needsDest && !finalDest) {
          setError('Destination required');
          setStage('result');
          return;
        }
        setDest(finalDest);
        setStage('running');
        runRpc(finalDest);
        return;
      }
      if (key.backspace || key.delete) {
        setDest((d) => d.slice(0, -1));
        return;
      }
      if (input) setDest((d) => d + input);
    } else if (stage === 'result') {
      if (key.escape) {
        clearScreen();
        return handleBack();
      }
      if (key.return) {
        onExit();
        exit({exitCode: 0});
      }
    }
  });

  const branchDisplay = session.branch ? `${BRANCH_ICON} ${session.branch}` : '';

  return h(
    Box,
    {flexDirection: 'column'},
    h(Text, null,
      chalk.bgBlue.black(` ${action.toUpperCase()} `), ' ',
      colorize.project(session.project || ''), ' ',
      colorize.branch(branchDisplay)
    ),
    h(Text, null, ...formatSessionHeader(session)),
    renderPreview(session.preview),
    stage === 'prompt'
      ? (() => {
          const defaultPathHint = action === 'export' ? session.default_export_path : null;
          return h(
            Box,
            {flexDirection: 'column', marginTop: 1},
            h(
              Text,
              null,
              action === 'export'
                ? 'Enter path to export (blank = default below). Must end in .txt.'
                : 'Enter destination file or directory path:'
            ),
            h(Text, null, '>', ' ', dest || chalk.dim('type path...')),
            defaultPathHint ? h(Text, {dimColor: true}, `Default (blank = use): ${defaultPathHint}`) : null,
            h(Text, {dimColor: true}, exitOnBack ? 'Enter: run  Esc: back to search' : 'Enter: run  Esc: back')
          );
        })()
      : h(
          Box,
          {flexDirection: 'column', marginTop: 1},
          error
        ? h(Text, {color: 'red'}, error)
        : h(
            Box,
            {flexDirection: 'column'},
            h(Text, {color: 'green'}, message || 'Done'),
            resultPath && resultPath !== message && h(Text, {dimColor: true}, resultPath)
          ),
          h(Text, {dimColor: true}, exitOnBack ? 'Enter/Esc: back' : 'Enter: exit  Esc: back')
        )
  );
}

function App() {
  const {exit} = useApp();
  const {stdout} = useStdout();
  // Determine initial screen:
  // 1. startScreen param takes priority (e.g., 'resume' for aichat trim)
  // 2. startAction or single session -> 'action'
  // 3. Otherwise -> 'results'
  const [screen, setScreen] = useState(
    startScreen || (startAction || sessions.length === 1 ? 'action' : 'results')
  );
  const [current, setCurrent] = useState(
    focusId ? Math.max(0, sessions.findIndex((s) => s.session_id === focusId)) : 0
  );
  const [selectedSession, setSelectedSession] = useState(null); // Directly store selected session
  // Initialize nonLaunch if starting at nonlaunch screen with directAction
  const [nonLaunch, setNonLaunch] = useState(
    startScreen === 'nonlaunch' && directAction ? {action: directAction} : null
  );
  // Track where we entered trim from; 'direct' means from Rust search (back exits)
  const [trimSource, setTrimSource] = useState(
    startScreen === 'trim' && directAction ? 'direct' : null
  );

  const safeCurrent = React.useMemo(() => {
    if (!sessions.length) return 0;
    return Math.min(Math.max(current, 0), sessions.length - 1);
  }, [current]);

  // Use directly selected session if available, otherwise compute from index
  const session = selectedSession || sessions[safeCurrent];

  const clearScreen = React.useCallback(() => {
    try {
      if (stdout?.isTTY) {
        stdout.write('\u001b[2J');
        stdout.write('\u001b[H');
      }
    } catch (e) {
      /* ignore */
    }
  }, [stdout]);

  const switchScreen = (next) => {
    clearScreen();
    setScreen(next);
  };

  const quit = () => exit({exitCode: 0});

  const backToOptions = () => {
    fs.writeFileSync(outPath, JSON.stringify({action: 'back_to_options'}));
    exit({exitCode: 0});
  };

  const finish = (action, kwargs = {}) => {
    writeResult(session.session_id, action, kwargs);
    exit({exitCode: 0});
  };

  // Handle find_options screen first (doesn't need sessions)
  if (screen === 'find_options') {
    return h(FindOptionsForm, {
      initialOptions: findOptions,
      variant: findVariant,
      onSubmit: (opts) => {
        fs.writeFileSync(outPath, JSON.stringify({find_options: opts}));
        exit({exitCode: 0});
      },
      onCancel: () => exit({exitCode: 0}),
    });
  }

  // Handle trim_confirm screen (confirmation after trim creates new file)
  if (screen === 'trim_confirm') {
    return h(TrimConfirmView, {
      trimInfo,
      clearScreen,
      onDone: (action) => {
        // action is 'resume' or 'delete'
        fs.writeFileSync(outPath, JSON.stringify({trim_action: action}));
        exit({exitCode: 0});
      },
      onCancel: () => {
        // Escape pressed - exit without action (file remains)
        fs.writeFileSync(outPath, JSON.stringify({trim_action: 'cancel'}));
        exit({exitCode: 0});
      },
    });
  }

  // Handle dir_confirm screen (confirmation when session is from different directory)
  if (screen === 'dir_confirm') {
    return h(DirConfirmView, {
      dirInfo,
      clearScreen,
      onDone: (choice) => {
        // choice is 'yes' (change dir) or 'no' (don't change)
        fs.writeFileSync(outPath, JSON.stringify({dir_choice: choice}));
        exit({exitCode: 0});
      },
      onCancel: () => {
        // Escape pressed - cancel action
        fs.writeFileSync(outPath, JSON.stringify({dir_choice: 'cancel'}));
        exit({exitCode: 0});
      },
    });
  }

  if (!sessions.length) {
    exit({exitCode: 0});
    return null;
  }

  let view = null;

  if (screen === 'results') {
    view = h(ResultsView, {
      focusIndex: current,  // Pass parent's index to sync selection
      onSelect: (idx) => {
        const selected = sessions[idx];
        setSelectedSession(selected); // Store session directly
        setCurrent(idx);
        // If directAction is set, route to appropriate screen (same logic as ActionView.onDone)
        if (directAction) {
          if (['path', 'copy', 'export'].includes(directAction)) {
            setNonLaunch({action: directAction});
            switchScreen('nonlaunch');
          } else if (directAction === 'query') {
            switchScreen('query');
          } else if (directAction === 'resume_menu') {
            switchScreen('resume');
          } else if (directAction === 'suppress_resume') {
            // Trim: go to trim form, with back exiting to Rust search
            setTrimSource('direct');  // Special value for direct invocation
            switchScreen('trim');
          } else {
            // Actions like smart_trim_resume execute directly
            finish(directAction);
          }
          return;
        }
        switchScreen(selectTarget);
      },
      onChangeIndex: (idx) => setCurrent(idx),
      onQuit: backToOptions,
      clearScreen,
    });
  } else if (screen === 'action') {
    view = h(ActionView, {
      session,
      onBack: () => {
        // If only 1 session, exit to shell; otherwise go to results
        if (sessions.length === 1) quit();
        else switchScreen('results');
      },
      onDone: (action) => {
        if (['path', 'copy', 'export'].includes(action)) {
          setNonLaunch({action});
          switchScreen('nonlaunch');
        } else if (action === 'query') {
          switchScreen('query');
        } else if (action === 'resume_menu') switchScreen('resume');
        else finish(action);
      },
      clearScreen,
    });
  } else if (screen === 'resume') {
    // If started directly on resume with single session, quit on back
    // If selectTarget was 'resume', we came from results; back goes to results
    // Otherwise we came from action menu; back goes to action
    const resumeBackTarget = selectTarget === 'resume' ? 'results' : 'action';
    view = h(ResumeView, {
      session,
      onBack: () => {
        if (startScreen === 'resume' && sessions.length === 1) quit();
        else switchScreen(resumeBackTarget);
      },
      onDone: (value) => {
        if (value === 'suppress_resume') {
          setTrimSource('resume');
          switchScreen('trim');
        } else if (value === 'smart_trim_resume') {
          setTrimSource('resume');
          switchScreen('smart_trim_form');
        } else if (value === 'continue') switchScreen('lineage');
        else finish(value);
      },
      clearScreen,
    });
  } else if (screen === 'lineage') {
    view = h(LineageView, {
      session,
      rpcPath,
      onContinue: () => switchScreen('continue_form'),
      onBack: () => switchScreen(lineageBackTarget),
      clearScreen,
    });
  } else if (screen === 'continue_form') {
    // If directAction is set, back exits to Rust search; otherwise go to lineage
    const continueBack = directAction ? quit : () => switchScreen('lineage');
    view = h(ContinueForm, {
      onBack: continueBack,
      onSubmit: (opts) => finish('continue', opts),
      session,
      clearScreen,
    });
  } else if (screen === 'smart_trim_form') {
    // If directAction is set, back exits to Rust search; otherwise quit
    const smartTrimBack = directAction ? quit : quit;
    view = h(SmartTrimForm, {
      onBack: smartTrimBack,
      onSubmit: (opts) => finish('smart_trim_resume', opts),
      session,
      clearScreen,
    });
  } else if (screen === 'trim') {
    // If trimSource is 'direct', back exits to Rust search; otherwise go to trimSource screen
    const trimBack = trimSource === 'direct' ? quit : () => switchScreen(trimSource || 'resume');
    view = h(TrimForm, {
      onBack: trimBack,
      onSubmit: (opts) => finish('suppress_resume', opts),
      session,
      clearScreen,
    });
  } else if (screen === 'trim_menu') {
    // Trim-only menu (for aichat trim command)
    // If selectTarget was 'trim_menu', we came from results; back goes to results
    // Otherwise just exit
    const trimBackTarget = selectTarget === 'trim_menu' ? 'results' : null;
    view = h(TrimView, {
      session,
      onBack: () => trimBackTarget ? switchScreen(trimBackTarget) : exit({exitCode: 0}),
      onDone: (value) => {
        if (value === 'suppress_resume') {
          setTrimSource('trim_menu');
          switchScreen('trim');
        } else if (value === 'smart_trim_resume') {
          setTrimSource('trim_menu');
          switchScreen('smart_trim_form');
        } else finish(value);
      },
      clearScreen,
    });
  } else if (screen === 'query') {
    // If we came via directAction, back goes to results; otherwise to action menu
    const queryBackTarget = directAction ? 'results' : 'action';
    view = h(QueryView, {
      session,
      rpcPath,
      onBack: () => switchScreen(queryBackTarget),
      onExit: quit,
      clearScreen,
      exitOnBack: !!directAction,  // Exit completely when invoked from Rust search
    });
  } else if (screen === 'nonlaunch') {
    // If we came via directAction, back goes to results; otherwise to action menu
    const nonlaunchBackTarget = directAction ? 'results' : 'action';
    view = h(NonLaunchView, {
      session,
      action: nonLaunch.action,
      rpcPath,
      onBack: () => switchScreen(nonlaunchBackTarget),
      onExit: quit,
      clearScreen,
      exitOnBack: !!directAction,  // Exit completely when invoked from Rust search
    });
  }

  return view || null;
}

render(h(App));
