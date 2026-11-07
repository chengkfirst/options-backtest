/* ui/drawer.js — the right-hand drawer: detail without navigating away.
 *
 * The design plan's fifth principle, and its rule 3: "Drill down without navigating away.
 * Details open in a right-hand drawer or as highlights on the chart; filters and selection
 * persist." The drawer is the whole mechanism for that — a trade, a run or a variant opens
 * beside the page it came from, and the page keeps its brush, its filters and its scroll.
 *
 * Three behaviours the plan states and this owns, because every one of them is the kind of
 * thing each caller would otherwise implement slightly differently:
 *
 * - **400 px, draggable to 560 px, expandable to full screen.** The widths are tokens.
 * - **Closing returns focus and scroll to where the user was.** The addendum's interaction
 *   rules. Without it, closing a card from the bottom of a four-hundred-row table lands the
 *   reader at the top of the page, and the row they were reading is gone.
 * - **Motion only for a change of place**, 150 to 200 ms, and never on the data.
 *
 * It holds no opinion about content: a caller hands it a title and markup and gets told when
 * it closes.
 */
"use strict";

import {$, esc} from "../dom.js";

let host = null;
let onClose = null;
let returnTo = null;   // the element that had focus when the drawer opened
let returnScroll = 0;

function frame(){
  if(host) return host;
  host = document.createElement("aside");
  host.className = "drawer";
  host.hidden = true;
  host.setAttribute("role", "complementary");
  document.body.append(host);

  // The divider: dragging the left edge widens the drawer between the two tokens. The
  // design plan asks for draggable dividers that remember their position, so the width
  // lives in localStorage — a per-viewer convenience, read defensively because a browser
  // with site data blocked throws on access rather than returning nothing.
  host.addEventListener("pointerdown", event => {
    if(!event.target.closest(".drawer-grip")) return;
    const startX = event.clientX;
    const startWidth = host.getBoundingClientRect().width;
    const move = e => {
      const want = startWidth + (startX - e.clientX);
      const lo = 320, hi = 560;
      host.style.width = `${Math.max(lo, Math.min(hi, want))}px`;
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      try{
        localStorage.setItem("drawerWidth", host.style.width);
      }catch{
        // Not remembered, which is not worth a notice: the drag still applied.
      }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });

  return host;
}

/**
 * Open the drawer.
 *
 * @param {object} spec {title, subtitle, body, actions} — all markup except `title`, which
 *                      is text. `actions` is the row of controls under the title.
 * @param {function} closed called when the drawer closes, however it closes.
 */
export function openDrawer(spec, closed){
  const node = frame();
  if(node.hidden){
    // Only on the way in: reopening for the next trade must not forget where the first one
    // was opened from, which is where J and K land the reader when they close the card.
    returnTo = document.activeElement;
    returnScroll = window.scrollY;
    try{
      const saved = localStorage.getItem("drawerWidth");
      if(saved) node.style.width = saved;
    }catch{
      // The default width from the token stands.
    }
  }
  onClose = closed || null;
  node.hidden = false;
  document.body.classList.add("drawer-open");
  // Two rows, not one. The card's own actions and the drawer's own controls were in the same
  // flex row, so three chips pushed the close button onto a second line and left it sitting
  // under the title with nothing beside it — the one control a reader looks for first, in the
  // place they look last. The drawer's controls now stay on the title's line whatever the
  // card offers, and the card's actions get a row of their own.
  node.innerHTML = `
    <div class="drawer-grip" aria-hidden="true"></div>
    <header class="drawer-head">
      <div class="drawer-titles">
        <h3>${esc(spec.title)}</h3>
        ${spec.subtitle ? `<p class="drawer-sub">${spec.subtitle}</p>` : ""}
      </div>
      <div class="drawer-controls">
        <button type="button" class="icon-btn" id="drawerFull"
          data-tip="Expand the card to the full window (F). Esc closes it.">⤢</button>
        <button type="button" class="icon-btn" id="drawerClose"
          data-tip="Close the card (Esc) and go back to the row it came from.">✕</button>
      </div>
    </header>
    ${spec.actions ? `<div class="drawer-actions">${spec.actions}</div>` : ""}
    <div class="drawer-body" id="drawerBody">${spec.body}</div>`;
  $("drawerClose").addEventListener("click", closeDrawer);
  $("drawerFull").addEventListener("click", toggleFull);
  return $("drawerBody");
}

/** Replace the body without re-opening — walking from one trade to the next. */
export function setBody(html){
  const body = $("drawerBody");
  if(body) body.innerHTML = html;
  return body;
}

export function isOpen(){
  return Boolean(host) && !host.hidden;
}

export function toggleFull(){
  if(host) host.classList.toggle("full");
}

export function closeDrawer(){
  if(!host || host.hidden) return;
  host.hidden = true;
  host.classList.remove("full");
  document.body.classList.remove("drawer-open");
  const back = onClose;
  onClose = null;
  // Focus and scroll first, then tell the caller: the caller may repaint the table, and a
  // repaint after the focus call would throw the focus away again.
  if(returnTo && document.body.contains(returnTo)) returnTo.focus();
  window.scrollTo({top: returnScroll});
  if(back) back();
}
