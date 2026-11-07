/* dom.js — the few DOM helpers everything else uses.
 *
 * Kept tiny on purpose. There is no framework here: the interface is generated from
 * backend definitions and rendered as strings, and a layer of abstraction over
 * `innerHTML` would buy nothing but indirection.
 */
"use strict";

export const $ = id => document.getElementById(id);

/** Escape for insertion as text **or into an attribute**.
 *
 * Every backend string goes through this before reaching innerHTML: a reason or a note
 * comes from a config a person wrote, and `<` in one of them must not become markup.
 *
 * It escaped `&` and `<` only, which is enough for text content and **not** for an
 * attribute — and this interface writes fifteen of them, `title="${esc(…)}"` and the rest.
 * A value containing a double quote closed the attribute and everything after it was read
 * as more attributes, so a strategy named `" onload="…` produced a live handler. Quotes
 * and `>` are escaped too now, which costs nothing in text position and removes the need
 * for a caller to know which of the two it is writing. */
export const esc = t => String(t ?? "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;")
  .replace(/'/g, "&#39;");

/** Escape, then honour the `**bold**` the backend's own reports use, and newlines. The
 *  boundary lines and verdicts are written with it, and they are the sentences that
 *  decide whether the numbers can be used at all. */
export const mark = t => esc(t)
  .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
  .replace(/\n/g, "<br>");

/** Set a node's content, or hide it when there is nothing to show. Returns whether
 *  anything was shown, so a caller can keep its section header in step. */
export function fill(node, html){
  if(!node) return false;
  const has = Boolean(html);
  node.innerHTML = has ? html : "";
  return has;
}

/** Show or hide a section together with its content. The browser's `[hidden]` is
 *  overridden by any type selector with a `display`, so app.css forces it; this is the
 *  one place that toggles it. */
export function show(node, visible){
  if(node) node.hidden = !visible;
}

/** Delegate a click to whatever matches `selector` inside `root`.
 *  Rendering replaces whole subtrees, so a listener bound to an element is lost on the
 *  next render; delegation survives it. */
export function onClick(root, selector, handler){
  if(!root) return;
  root.addEventListener("click", e => {
    const hit = e.target.closest(selector);
    if(hit && root.contains(hit)) handler(hit, e);
  });
}
