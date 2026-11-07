/* ui/palette.js — one box that reaches every object and every action.
 *
 * The design plan: "Cmd/Ctrl + K opens the command palette: jump to an idea, run, trade_id,
 * variant or underlying, and run actions (submit, compare, ablate slot, open robustness)."
 *
 * It exists for a reason that is easy to miss. A researcher runs dozens of backtests a day
 * and the thing they do most is **go back to one** — a run from this morning, the trade they
 * were reading before lunch. Every other route to it is a page, a list and a scroll; this is
 * a name and a key. The same box runs the actions, because "what can I do here" and "where
 * can I go" are one question when you have forgotten the word for either.
 *
 * Views do not call into it. They **offer** a source — a function from the typed text to a
 * list of things — so the palette knows nothing about runs, trades or slots, and a source
 * that is gone (the run page left) takes its entries with it.
 */
"use strict";

import {$, esc} from "../dom.js";
import {pretty, shortcuts} from "./keys.js";

/** name -> (query) => [{label, hint, kind, run}] */
const SOURCES = new Map();

/** Sources that have told the box they are still fetching. */
const PENDING = new Set();

/** Offer things to the palette for as long as the returned function is not called. */
export function provide(name, source){
  SOURCES.set(name, source);
  return () => { SOURCES.delete(name); PENDING.delete(name); };
}

/** A source saying "I have no answer **yet**", which is a different statement from "there is
 *  nothing" and has to read as one. */
export function fetching(name, isFetching){
  if(isFetching) PENDING.add(name); else PENDING.delete(name);
}

const waiting = () => PENDING.size > 0;

let host = null;
let items = [];
let at = 0;

function frame(){
  if(host) return host;
  host = document.createElement("div");
  host.className = "palette";
  host.hidden = true;
  host.innerHTML = `
    <div class="palette-box" role="dialog" aria-label="go to, or do">
      <input id="paletteQuery" class="palette-input" type="text" autocomplete="off"
             spellcheck="false" placeholder="a run, a trade, a page, or something to do">
      <ul class="palette-list" id="paletteList"></ul>
      <p class="palette-foot">↑↓ to move · ↩ to go · Esc to close</p>
    </div>`;
  document.body.append(host);
  host.addEventListener("click", event => {
    if(event.target === host) close();
    const row = event.target.closest("[data-at]");
    if(row){ at = +row.dataset.at; choose(); }
  });
  const input = $("paletteQuery");
  input.addEventListener("input", () => { at = 0; paint(); });
  input.addEventListener("keydown", event => {
    if(event.key === "ArrowDown"){ event.preventDefault(); at = Math.min(at + 1, items.length - 1); paint(); }
    else if(event.key === "ArrowUp"){ event.preventDefault(); at = Math.max(at - 1, 0); paint(); }
    else if(event.key === "Enter"){ event.preventDefault(); choose(); }
    else if(event.key === "Escape"){ event.preventDefault(); close(); }
  });
  return host;
}

/** The commands, from the live shortcut registry rather than a second list of their own. */
function commands(query){
  const q = query.toLowerCase();
  return shortcuts()
    .filter(s => s.does && s.does.toLowerCase().includes(q))
    .map(s => ({label: s.does, hint: pretty(Array.isArray(s.keys) ? s.keys[0] : s.keys),
                kind: s.scope, run: s.run}));
}

function gather(query){
  const out = [];
  for(const source of SOURCES.values()){
    try{
      out.push(...(source(query) || []));
    }catch{
      // A source that throws must not take the palette with it: the box is how a reader
      // navigates when they are already lost.
    }
  }
  out.push(...commands(query));
  return out.slice(0, 40);
}

function paint(){
  const query = $("paletteQuery").value.trim();
  items = gather(query);
  at = Math.max(0, Math.min(at, items.length - 1));
  $("paletteList").innerHTML = items.length
    ? items.map((it, i) => `<li class="palette-row${i === at ? " on" : ""}" data-at="${i}">
        <span class="palette-kind">${esc(it.kind || "")}</span>
        <span class="palette-label">${esc(it.label)}</span>
        <span class="palette-hint">${esc(it.hint || "")}</span></li>`).join("")
    : `<li class="palette-empty">${waiting()
        ? "Still reading the run list…"
        : `Nothing matches “${esc(query)}”. Runs, trades, pages and every action with a
           shortcut are in here.`}</li>`;
}

function choose(){
  const it = items[at];
  if(!it) return;
  close();
  it.run();
}

/** Redraw the list, for a source whose answer arrives after the question.
 *
 * A source that has to fetch cannot answer the first keystroke, and returning nothing made
 * the box say "nothing matches 170" about a run that exists — a wrong answer, given
 * confidently, to someone who then stops looking. It returns nothing and calls this when the
 * answer lands, and the reader sees the row appear under what they typed. */
export function repaint(){
  if(isOpen()) paint();
}

export function open(){
  frame().hidden = false;
  const input = $("paletteQuery");
  input.value = "";
  at = 0;
  paint();
  input.focus();
}

export function close(){
  if(host) host.hidden = true;
}

export function isOpen(){
  return Boolean(host) && !host.hidden;
}
