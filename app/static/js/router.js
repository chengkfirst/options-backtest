/* router.js — which surface is on screen, kept in the URL.
 *
 * The design plan asks for one thing of navigation: "Any run, trade or comparison
 * reachable by URL with view state restored", at 100%. So the URL is not a side effect of
 * navigating, it is **where the view state lives** — the route, the selected tab, the
 * brushed range, the filters, the open drawer. Rendering reads it; interacting writes it;
 * browser back and forward work because nothing else holds the state.
 *
 * Hash routing rather than paths: the service is a stdlib HTTP server serving a
 * directory, and a path route would need it to answer every URL with index.html. A hash
 * costs nothing and the server stays a file server.
 *
 * A route looks like `#/runs/12?tab=overview&range=2024-01-01..2024-03-31`.
 */
"use strict";

const ROUTES = [];
let current = null;

/** Register a surface.
 *
 * `pattern` is a path with `:name` placeholders (`/runs/:id`). `enter` is called with
 * ({params, query}) when the route is entered or its query changes; `leave`, if given,
 * when another route takes over.
 */
export function route(pattern, enter, leave){
  const names = [];
  const rx = new RegExp("^" + pattern.replace(/:(\w+)/g, (_, n) => {
    names.push(n);
    return "([^/]+)";
  }) + "$");
  ROUTES.push({pattern, rx, names, enter, leave});
}

/** The current hash, split into a path and a query. */
export function location_(){
  const raw = window.location.hash.replace(/^#/, "") || "/";
  const [path, search = ""] = raw.split("?");
  return {path: path || "/", query: Object.fromEntries(new URLSearchParams(search))};
}

/** Go to a route, adding a history entry. */
export function go(path, query){
  window.location.hash = toHash(path, query);
}

/** Change the view state **and** leave a step in the history.
 *
 * For the parts a reader moves between on purpose — a tab, a selected trade — so that back
 * returns to the one before rather than leaving the run entirely. `replace` stays for
 * everything continuous.
 *
 * The merge is the same as `replace`'s: a step that dropped the brushed range would make
 * going back to the previous tab arrive at a different reading from the one left behind.
 */
export function step(query){
  const {path, query: was} = location_();
  const merged = {...was, ...query};
  for(const [k, v] of Object.entries(merged))
    if(v == null || v === "") delete merged[k];
  const hash = toHash(path, merged);
  if(hash === window.location.hash) return;
  // `hash =` pushes a history entry and fires hashchange, which re-enters the route — and
  // the route is what renders the new tab, so nothing here renders it a second time.
  window.location.hash = hash;
}

/** Change the view state **without** adding a history entry.
 *
 * For a brush or a filter: a drag produces dozens of these a second, and each one in the
 * history would bury the last real navigation under a hundred back-button steps. The URL
 * still describes the view, so a copied link opens what is on screen.
 *
 * A tab switch is **not** one of these — see `step` below. The addendum's state table asks
 * that view state in the URL make back and forward work, and a tab is the one piece of it
 * a reader moves between deliberately and expects to be able to undo.
 */
export function replace(query){
  const {path, query: was} = location_();
  const merged = {...was, ...query};
  for(const [k, v] of Object.entries(merged))
    if(v == null || v === "") delete merged[k];
  const hash = toHash(path, merged);
  window.history.replaceState(null, "", hash);
  // replaceState fires no hashchange, which is the point: the view asked for this and has
  // already rendered it. Re-entering the route here would redraw it a second time.
  if(current) current.query = merged;
}

function toHash(path, query){
  const search = new URLSearchParams(
    Object.entries(query || {}).filter(([, v]) => v != null && v !== "")).toString();
  return "#" + path + (search ? "?" + search : "");
}

/** What the current route was entered with, so a view can read its own state back. */
export function params(){
  return current ? {...current.params} : {};
}

export function query(){
  return current ? {...current.query} : {};
}

function match(path){
  for(const r of ROUTES){
    const m = r.rx.exec(path);
    if(m) return {route: r, params: Object.fromEntries(r.names.map((n, i) => [n, m[i + 1]]))};
  }
  return null;
}

/** Start routing. `fallback` is the path to use when the hash names no known route —
 *  including the empty hash on first load. */
export function start(fallback = "/"){
  const render = () => {
    const {path, query: q} = location_();
    const hit = match(path) || match(fallback);
    if(!hit){
      // Nothing matched and the fallback is not a route either: a programming error, and
      // one that would otherwise leave an empty page with no explanation.
      throw new Error(`no route matches ${path}, and the fallback ${fallback} is not a `
        + `registered route either`);
    }
    const changed = !current || current.route !== hit.route;
    if(changed && current?.route.leave) current.route.leave();
    current = {route: hit.route, params: hit.params, query: q};
    // **A new surface opens at its top.** The browser keeps the scroll position across a
    // hash change, so ticking two runs at the bottom of a hundred-row log and pressing
    // compare landed the reader in the middle of the comparison's trade table — below the
    // columns, below the differences, below the result, with nothing on screen saying what
    // page they were on. Only on a change of surface: a tab switch inside a run is a step
    // within one page and must keep its place.
    if(changed) window.scrollTo({top: 0});
    hit.route.enter({params: hit.params, query: q, changed});
  };
  window.addEventListener("hashchange", render);
  render();
}
