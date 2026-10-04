/* app.js — the shell: boot, the route table, the theme, the keyboard.
 *
 * The one entry point. `index.html` loads this and nothing else, so the order the modules
 * come up in is written here rather than spread across script tags.
 *
 * The design plan's eight surfaces arrive one at a time. Today there is one — the bench —
 * and the others are registered as routes that say what they will be and what they are
 * waiting on, because a navigation item that silently does nothing is worse than one that
 * explains itself.
 */
"use strict";

import {$, onClick, show} from "./dom.js";
import {catchPageErrors, notice} from "./net.js";
import {go, location_, route, start} from "./router.js";
import * as bench from "./views/bench.js";

/* ── the surfaces ──
 * `view` is the id of the section to show; `pending` is what it is waiting on, for the
 * ones not built yet. Order is the order in the rail. */
const SURFACES = [
  {path: "/bench", icon: "▦", name: "bench", view: "view-bench",
   what: "compose a configuration, run it, read the result"},
  {path: "/log", icon: "▤", name: "experiment log", view: "view-pending",
   what: "every run grouped by idea, with its trial count and verdict",
   pending: "the stored runs, notes and verdicts exist in the backend; this surface is "
            + "phase 4 of the frontend plan"},
  {path: "/compare", icon: "◫", name: "compare", view: "view-pending",
   what: "two to four runs side by side, differences first",
   pending: "needs the experiment log to pick runs from; phase 4"},
  {path: "/registry", icon: "◈", name: "components", view: "view-pending",
   what: "every registered variant by slot, with its parameter schema",
   pending: "the registry exists in the backend (backtest/core/component.py); it needs a "
            + "/api/components endpoint, which is phase 3"},
  {path: "/data", icon: "◴", name: "data", view: "view-pending",
   what: "the data snapshot's coverage and quality",
   pending: "the loader's quality checks exist; there is no snapshot id to hang a page "
            + "on yet, so the realism strip carries the provenance instead"},
];

function renderRail(){
  const {path} = location_();
  $("rail").innerHTML = SURFACES.map(s => `
    <a class="rail-item${path === s.path ? " on" : ""}" href="#${s.path}"
       title="${s.name} — ${s.what}">
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
    <p class="hint">The bench does the whole loop today — compose, run, read, validate.</p>
    <button class="btn" type="button" data-goto="/bench">go to the bench</button>`;
}

function showOnly(viewId){
  for(const id of ["view-bench", "view-pending"]) show($(id), id === viewId);
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
  $("theme").title = `switch to the ${next} theme`;
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

$("railToggle").addEventListener("click", () => {
  document.body.classList.toggle("rail-open");
});
onClick(document.body, "[data-goto]", el => go(el.dataset.goto));

for(const surface of SURFACES){
  route(surface.path, () => {
    renderRail();
    showOnly(surface.view);
    if(surface.pending) renderPending(surface);
  });
}

start("/bench");

// The bench's own startup: probe the service, then load the definitions its form is
// generated from. Awaited at the top level of a module, which is allowed and is what the
// browser's deferred module loading is for.
try{
  await bench.init();
}catch(e){
  notice(`the bench could not start: ${e.message}`);
}
