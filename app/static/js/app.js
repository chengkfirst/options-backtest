/* app.js — the shell: boot, the route table, the theme, the keyboard.
 *
 * The one entry point. `index.html` loads this and nothing else, so the order the modules
 * come up in is written here rather than spread across script tags.
 *
 * The design plan's eight surfaces arrive one at a time. Built: the composer and the run
 * analysis. The rest are registered as routes that say what they will be and what they are
 * waiting on, because a navigation item that silently does nothing is worse than one that
 * explains itself.
 */
"use strict";

import {$, esc, onClick, show} from "./dom.js";
import {API, catchPageErrors, getJSON, notice} from "./net.js";
import {go, location_, route, start} from "./router.js";
import {bind, startKeys} from "./ui/keys.js";
import * as palette from "./ui/palette.js";
import {startTips} from "./ui/tip.js";
import * as bench from "./views/bench.js";
import * as compareView from "./views/compare.js";
import * as logView from "./views/log.js";
import * as runView from "./views/run.js";

/* ── the surfaces ──
 * `view` is the id of the section to show; `pending` is what it is waiting on, for the ones
 * not built yet; `page` names the module that renders itself, for a surface that fetches.
 * Order is the order in the rail. */
const SURFACES = [
  {path: "/bench", icon: "▦", name: "bench", view: "view-bench",
   what: "write a configuration and run it. The result opens on its own page"},
  {path: "/log", icon: "▤", name: "experiment log", view: "view-log",
   what: "every run grouped by idea, with the size of the search under it",
   page: () => logView},
  {path: "/compare", icon: "◫", name: "compare", view: "view-compare",
   what: "two to four runs side by side, differences first",
   page: () => compareView},
  {path: "/registry", icon: "◈", name: "variants", view: "view-pending",
   what: "every registered variant by slot, with its parameter schema",
   pending: "the registry exists; no endpoint serves it yet"},
  {path: "/selftest", icon: "✓", name: "self test", external: "/selftest.html",
   what: "every module run against the cases that have broken them"},
  {path: "/data", icon: "◴", name: "data", view: "view-pending",
   what: "the data snapshot's coverage and quality",
   pending: "the quality checks exist; there is no snapshot id to hang a page on"},
];

function renderRail(){
  const {path} = location_();
  $("rail").innerHTML = SURFACES.map(s => `
    <a class="rail-item${path === s.path ? " on" : ""}" href="${s.external || "#" + s.path}"
       data-tip="${esc(s.name)}${s.pending ? " — not built yet" : ""}">
      <span class="rail-icon" aria-hidden="true">${s.icon}</span>
      <span class="rail-name">${s.name}</span>
    </a>`).join("");
}

/** The surface that is not built yet says so, and says what it is waiting on. The design
 *  plan's state table forbids the words "no data" alone, and criterion 2.7 of the
 *  requirements asks that a feature which is not built says so on the interface. */
function renderPending(surface){
  $("pendingBody").innerHTML = `
    <h2>${surface.name}</h2>
    <p class="lead">${surface.what}</p>
    <p class="hint"><b>Not built yet.</b> ${surface.pending}</p>
    <button class="btn" type="button" data-goto="/bench">go to the bench</button>`;
}

const VIEWS = ["view-bench", "view-pending", "view-run", "view-log", "view-compare"];

function showOnly(viewId){
  for(const id of VIEWS) show($(id), id === viewId);
}

/* ── the theme ──
 * Dark is the default (the design plan's section 12). The choice is remembered per
 * browser, which is a per-viewer convenience and not research state, so localStorage is
 * the right place for it — and it is read defensively, because a browser that blocks site
 * data throws on access rather than returning nothing. */
const THEMES = ["dark", "light"];

function applyTheme(name){
  document.documentElement.dataset.theme = name;
  try{
    localStorage.setItem("theme", name);
  }catch{
    // A browser with site data blocked. The theme still applies for this page load; it
    // simply will not be remembered, which is not worth a notice.
  }
  const next = THEMES[(THEMES.indexOf(name) + 1) % THEMES.length];
  $("theme").textContent = next === "dark" ? "dark" : "light";
  $("theme").dataset.tip = `Switch to the ${next} theme. Dark is the default.`;
}

function initTheme(){
  let saved = null;
  try{
    saved = localStorage.getItem("theme");
  }catch{
    saved = null;
  }
  applyTheme(THEMES.includes(saved) ? saved : "dark");
  $("theme").addEventListener("click", () => {
    const now = document.documentElement.dataset.theme || "dark";
    applyTheme(THEMES[(THEMES.indexOf(now) + 1) % THEMES.length]);
  });
}

/* ── boot ── */
catchPageErrors();
initTheme();
startKeys();

/* Every run, reachable from anywhere.
 *
 * The experiment log offers its own rows while it is open, and that is not enough: the plan
 * asks the palette to reach a run "from anywhere", and the moment a reader needs one by
 * number is the moment they are on another page reading about it. `null` means "not read
 * yet"; opening the palette sets it back to `null` so the list is current.
 */
let KNOWN_RUNS = null;

/* The keys that belong to no one surface. Registered first, so a surface's own Escape
   shadows this one while that surface is open — the registry looks at the most recently
   registered scope first. */
bind("global", [
  {keys: ["mod+k"], does: "open the command palette",
   // Opening re-reads the run list. It was read once and kept for the session, so a
   // researcher who ran six backtests after opening the box once could not find any of them
   // in it — the one list that must be current is the one you reach for when you have lost
   // your place. One small request to a local service, and only when the box is opened.
   run: () => {
     if(palette.isOpen()){ palette.close(); return; }
     KNOWN_RUNS = null;
     palette.open();
   }},
  {keys: ["Escape"], does: "close the command palette",
   run: palette.close, where: palette.isOpen},
  // Deliberately no single-letter navigation. A bare key that leaves the page is the one
  // shortcut nobody wants to discover by accident while a configuration is half written, and
  // the palette reaches every surface by name already.
]);

// The surfaces themselves, so the palette can reach a page by name as well as a run by id.
palette.provide("surfaces", query => {
  const q = String(query || "").toLowerCase();
  return SURFACES.filter(s => s.name.includes(q) || s.path.includes(q))
    .map(s => ({kind: "page", label: s.name, hint: s.path,
                run: () => (s.external ? window.location.assign(s.external) : go(s.path))}));
});

palette.provide("runs", query => {
  const q = String(query || "").trim().toLowerCase();
  if(!q) return [];
  if(KNOWN_RUNS === null){
    KNOWN_RUNS = [];                       // only one fetch, whatever happens to it
    palette.fetching("runs", true);
    getJSON(API.runs, {limit: 500})
      .then(data => { KNOWN_RUNS = data.runs || []; })
      .catch(e => notice(`the palette could not read the run list: ${e.message}`))
      // Whatever happened, the box stops saying it is still reading — and redraws, so the
      // rows appear under what the reader has already typed rather than after another key.
      .finally(() => { palette.fetching("runs", false); palette.repaint(); });
    return [];
  }
  return KNOWN_RUNS
    .filter(r => String(r.id).includes(q)
      || String(r.strategy_name).toLowerCase().includes(q)
      || String(r.symbol).toLowerCase().includes(q))
    .slice(0, 8)
    .map(r => ({kind: "run", label: `${r.id} · ${r.strategy_name} · ${r.symbol}`,
                hint: String(r.started).slice(0, 10), run: () => go(`/runs/${r.id}`)}));
});
// The page explains itself by hovering, so the explanation is a real component rather
// than the browser's `title`.
startTips();

$("railToggle").addEventListener("click", () => {
  document.body.classList.toggle("rail-open");
});
onClick(document.body, "[data-goto]", el => go(el.dataset.goto));

for(const surface of SURFACES){
  route(surface.path, ({query}) => {
    renderRail();
    showOnly(surface.view);
    if(surface.pending) renderPending(surface);
    // A surface with a module of its own fetches what it needs and renders itself. The
    // failure goes to the problem strip rather than leaving the section empty, which is the
    // one outcome that says nothing.
    const module = surface.page?.();
    if(module) module.enter({query}).catch(e =>
      notice(`${surface.name} could not be opened: ${e.message}`));
  }, () => surface.page?.()?.leave?.());
}

/* One run, analysed. Not in the rail: it is reached from a run, not from a menu — but it
   is a first-class route, so the URL carries the tab, the brushed range and the mark, and
   a copied link opens the same view. */
route("/runs/:id", ({params, query}) => {
  renderRail();
  showOnly("view-run");
  runView.enter({params, query}).catch(e =>
    notice(`run ${params.id} could not be opened: ${e.message}`));
}, () => runView.leave());

start("/bench");

// The bench's own startup: probe the service, then load the definitions its form is
// generated from. Awaited at the top level of a module, which is allowed and is what the
// browser's deferred module loading is for.
try{
  await bench.init();
}catch(e){
  notice(`the bench could not start: ${e.message}`);
}
