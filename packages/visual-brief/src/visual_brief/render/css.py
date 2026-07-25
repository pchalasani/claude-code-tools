"""Stylesheet for self-contained visual brief pages."""

CSS = """
:root {
  color-scheme: light dark;
  --bg: #f7f6f2; --paper: #fffefa; --ink: #24231f; --muted: #67645d;
  --line: #d9d6cd; --soft: #efede6; --focus: #315f78;
} @media (prefers-color-scheme: dark) {
  :root {
    --bg: #171714; --paper: #201f1b; --ink: #ece9df; --muted: #aaa69b;
    --line: #454239; --soft: #2b2923; --focus: #8abbd1;
  } }
* { box-sizing: border-box; } html { background: var(--bg); }
body { color: var(--ink); background: var(--bg); margin: 0;
  font: 17px/1.65 ui-serif, Georgia, Cambria, "Times New Roman", serif;
}
.page { width: min(78ch, calc(100% - 2rem)); margin: 0 auto; padding: 4rem 0; }
h1,h2,h3,summary { font-family: ui-sans-serif,system-ui,-apple-system,sans-serif; }
h1 { margin: 0; font-size: clamp(2rem, 5vw, 3.2rem); line-height: 1.08; }
.deck { color: var(--muted); font-size: 1.12rem; margin: 1.25rem 0 2rem; }
.eyebrow { color: var(--muted); font: 700 .72rem/1.2 ui-sans-serif, system-ui;
  letter-spacing: .12em; margin-bottom: .8rem; text-transform: uppercase;
}
.legend { border-block: 1px solid var(--line); padding: .85rem 0;
  margin: 2rem 0 3rem;
  display: flex; flex-wrap: wrap; align-items: center; gap: .55rem .7rem;
}
.legend-label { color: var(--muted); font: 600 .78rem ui-sans-serif, system-ui; }
.key-controls {
  display: flex; flex-wrap: wrap; gap: .4rem; margin: 1.5rem 0 .8rem;
}
.key-control, #close-search, #close-help {
  background: transparent; border: 1px solid var(--line); border-radius: 4px;
  color: var(--muted); cursor: pointer; font: 650 .72rem system-ui;
  padding: .38rem .55rem;
}
.search-panel {
  align-items: center; background: var(--soft); border-radius: 5px;
  display: flex; flex-wrap: wrap; gap: .55rem; padding: .65rem;
}
.search-panel[hidden], [hidden] { display: none !important; }
.search-panel input {
  background: var(--paper); border: 1px solid var(--line); border-radius: 4px;
  color: var(--ink); flex: 1; font: inherit; min-width: 12rem; padding: .4rem;
}
#match-count { color: var(--muted); font: .75rem system-ui; }
dialog {
  background: var(--paper); border: 1px solid var(--line); border-radius: 8px;
  color: var(--ink); max-width: min(32rem, calc(100% - 2rem)); padding: 1.4rem;
}
dialog::backdrop { background: rgb(0 0 0 / .55); }
dialog h2 { margin-top: 0; }
dialog dl { display: grid; grid-template-columns: 6rem 1fr; gap: .35rem .8rem; }
dialog dt { font: 750 .8rem system-ui; }
dialog dd { margin: 0; }
.chip {
  border: 1px solid currentColor; border-radius: 999px; display: inline-block;
  font: 700 .69rem/1 ui-sans-serif, system-ui; padding: .33rem .48rem;
  white-space: nowrap; }
.verified-by-me { color: #18704a; background: #dff4e8; }
.reported-by-agent { color: #285f99; background: #e3effc; }
.unverified { color: #8b6200; background: #fff0bd; }
.known-limitation { color: #8d3f48; background: #f9dfe2; }
.answered { color: #5b3ea8; background: #ece4fb; }
@media (prefers-color-scheme: dark) {
  .verified-by-me { color: #8bd8b2; background: #193b2c; }
  .reported-by-agent { color: #9ac7f4; background: #1d344d; }
  .unverified { color: #f1cb69; background: #463813; }
  .known-limitation { color: #efa6ae; background: #49272b; }
  .answered { color: #c7b1f5; background: #2e2447; } }
.update { background: var(--paper); border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 12px 35px rgb(0 0 0 / .04); margin: 0 0 1.25rem; }
.update > summary { cursor: pointer; list-style: none; padding: 1.35rem 1.5rem; }
.update > summary::-webkit-details-marker { display: none; }
.update-head { display: flex; gap: 1rem; justify-content: space-between; }
.update-title { font-size: 1.25rem; font-weight: 720; line-height: 1.3; }
.time { color: var(--muted); font-size: .78rem; white-space: nowrap; }
.update-body { border-top: 1px solid var(--line); padding: 0 1.5rem 1.5rem; }
.update-summary { color: var(--muted); font-size: 1.04rem; margin: 1.35rem 0; }
.lane-shell {
  border-top: 1px solid var(--line); padding: .2rem 0; position: relative; }
.lane > summary { align-items: center; cursor: pointer; display: flex;
  font-weight: 720;
  justify-content: space-between; padding: .85rem 2.3rem .85rem 0;
}
.lane-name::before { color: var(--muted); content: "›"; margin-right: .55rem; }
.lane[open] > summary .lane-name::before { content: "⌄"; }
.ask-button {
  border: 1px solid var(--line); border-radius: 999px; color: var(--muted);
  background: transparent; cursor: pointer; font: 700 .8rem/1 system-ui;
  min-height: 1.7rem; min-width: 1.7rem; }
.ask-button:hover, .ask-button:focus-visible { border-color: var(--focus); }
.lane-shell>.ask-button,.item-shell>.ask-button {
  position: absolute; right: 0; top: .72rem; z-index: 1; }
.item-shell { position: relative; }
.item {
  border-left: 2px solid var(--line); margin: .25rem 0 1rem .45rem;
  padding: 0 0 0 1rem; }
.item > summary { cursor: pointer; list-style: none;
  padding: .7rem 2.3rem .7rem 0; }
.item > summary::-webkit-details-marker { display: none; }
.item-head { align-items: start; display: flex; gap: .6rem; }
.glance { flex: 1; font-weight: 650; line-height: 1.45; }
.explanation { margin: .2rem 0 1rem; }
.forensics {
  background: var(--soft); border-radius: 5px; margin: .7rem 0;
  padding: .2rem .8rem; }
.forensics > summary { cursor: pointer; font-size: .88rem; font-weight: 700; }
pre {
  border-left: 2px solid var(--line); overflow-x: auto; padding: .6rem .8rem;
  white-space: pre-wrap; word-break: break-word;
  font: .78rem/1.55 ui-monospace, SFMono-Regular, Consolas, monospace; }
.nested { border-left: 1px solid var(--line); margin: .55rem 0 .55rem .3rem; }
.nested > summary { cursor: pointer; font-weight: 650; padding-left: .7rem; }
.nested-body { color: var(--muted); padding: .2rem .7rem .6rem; }
.table-wrap { margin: 1rem 0; overflow-x: auto; }
table { border-collapse: collapse; font-size: .88rem; width: 100%; }
caption { font-weight: 700; text-align: left; margin-bottom: .4rem; }
th, td { border-bottom: 1px solid var(--line); padding: .55rem; text-align: left; }
th { font-family: ui-sans-serif, system-ui; font-size: .75rem; }
.wrong { color: #a33b36; font-weight: 800; }
.qa { border-top: 1px dashed var(--line); margin-top: .9rem; padding-top: .7rem; }
.qa-q, .qa-a { margin: .25rem 0; }
.qa-label { color: var(--muted); font: 700 .72rem system-ui; margin-right: .4rem; }
.thread {
  border-top: 1px dashed var(--line); margin-top: .9rem; padding-top: .35rem;
}
.thread > summary {
  align-items: center; cursor: pointer; display: flex; gap: .6rem;
  justify-content: space-between; list-style: none; padding: .4rem 0;
}
.thread-title { font-weight: 650; }
.thread-body { border-left: 2px solid var(--line); margin-left: .35rem;
  padding-left: .8rem; }
.turn { margin: .65rem 0; }
.turn p { margin: .15rem 0; white-space: pre-wrap; }
.turn-meta {
  color: var(--muted); display: flex; font: 700 .68rem system-ui;
  gap: .6rem; justify-content: space-between;
}
.turn.agent { background: var(--soft); border-radius: 4px; padding: .45rem .6rem; }
.question-box { display: none; margin: .7rem 0; }
.question-box.open, .question-box.reply-box { display: block; }
.question-box textarea {
  background: var(--bg); border: 1px solid var(--line); border-radius: 4px;
  color: var(--ink); font: inherit; min-height: 5rem; padding: .6rem; width: 100%; }
.actions, .signals { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .4rem; }
button.submit, .signal {
  background: transparent; border: 1px solid var(--line); border-radius: 4px;
  color: var(--muted); cursor: pointer; font: 650 .72rem system-ui;
  padding: .38rem .55rem;
}
button.submit { background: var(--ink); color: var(--paper); }
.pending { color: var(--muted); font-style: italic; margin: .6rem 0; }
.status { color: var(--muted); font: .72rem system-ui; min-height: 1em; }
:focus, .nav-focus { outline: 3px solid var(--focus); outline-offset: 3px; }
@media (max-width: 580px) {
  .page { padding-top: 2rem; }
  .update-head { display: block; }
  .time { display: block; margin-top: .35rem; }
}
"""
