/* ui/tip.js — the explanation that appears when you point at something.
 *
 * This page explains itself through hovering: what λ is, what a Calmar measures, why a
 * card is empty, what an interval containing zero means. All of it was carried by the
 * browser's own `title` attribute, which is the worst possible vehicle for it — about a
 * second of delay before it appears, an operating-system font nobody chose, no line
 * breaks, no width, and it vanishes the moment the pointer moves. Explanations that are
 * unpleasant to reach do not get read, and an interface whose help is unread is an
 * interface with no help.
 *
 * One element, moved and filled. A tooltip per target would be hundreds of nodes on a
 * page that re-renders on every brush.
 *
 * Written as `data-tip` rather than `title` so the native one does not also appear; an
 * element carrying both shows two tooltips, one of them late and ugly.
 */
"use strict";

const SHOW_AFTER = 120;     // ms. Long enough not to flicker while crossing the page,
                            // short enough to feel like an answer rather than a wait.
const EDGE = 8;             // keep the box this far inside the viewport

let node = null;
let timer = 0;
let current = null;

function box(){
  if(node) return node;
  node = document.createElement("div");
  node.className = "tip";
  node.hidden = true;
  node.setAttribute("role", "tooltip");
  document.body.append(node);
  return node;
}

function show(target){
  const text = target.dataset.tip;
  if(!text) return;
  const tip = box();
  tip.textContent = text;
  tip.hidden = false;

  // Measured after filling, because the height depends on how the text wrapped.
  const r = target.getBoundingClientRect();
  const t = tip.getBoundingClientRect();
  let left = r.left + r.width / 2 - t.width / 2;
  left = Math.max(EDGE, Math.min(left, window.innerWidth - t.width - EDGE));
  // Above by default; below when there is no room above, which is the case for anything
  // in the sticky header at the top of the page.
  const above = r.top - t.height - 10;
  const top = above > EDGE ? above : r.bottom + 10;
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
  tip.dataset.side = above > EDGE ? "above" : "below";
  current = target;
}

function hide(){
  clearTimeout(timer);
  current = null;
  if(node) node.hidden = true;
}

/** Start listening. One delegated pair of listeners for the whole document, so markup
 *  rendered later needs no wiring — which matters here, where every surface rebuilds its
 *  own HTML on each render. */
export function startTips(){
  document.addEventListener("pointerover", e => {
    const target = e.target.closest("[data-tip]");
    if(!target || target === current) return;
    clearTimeout(timer);
    timer = setTimeout(() => show(target), SHOW_AFTER);
  });
  document.addEventListener("pointerout", e => {
    const target = e.target.closest("[data-tip]");
    if(target && !target.contains(e.relatedTarget)) hide();
  });
  // Keyboard reachable: a tooltip only a mouse can summon is help that keyboard users do
  // not have.
  document.addEventListener("focusin", e => {
    const target = e.target.closest("[data-tip]");
    if(target) show(target);
  });
  document.addEventListener("focusout", hide);
  document.addEventListener("keydown", e => { if(e.key === "Escape") hide(); });
  // Anything that moves the page moves the target out from under the box.
  window.addEventListener("scroll", hide, {passive: true});
  window.addEventListener("resize", hide);
}
