/* charts/svg.js — the helpers every chart emits through.
 *
 * Here rather than in fmt.js because these emit SVG markup; fmt.js stays pure text.
 * Moved out of the single charts.js with the bodies unchanged.
 */
"use strict";

import {notice} from "../net.js";

/** Make one unit of the drawing equal one pixel on screen.
 *
 * These charts were written with a fixed `viewBox` — 520 or 1080 wide — and are laid out
 * at whatever width their column gives them. The browser then scales the whole drawing,
 * **text included**: at a measured 286 px against a 520 viewBox the labels rendered at
 * 5.5 px, against the design plan's 11 px minimum. Unreadable, and invisible to review
 * because the markup says `font-size="10"`.
 *
 * Setting the coordinate space to the measured size removes the scaling, so a size in the
 * markup is a size on screen. Returns the box to draw inside; the caller must use it
 * rather than its old constants, or half the chart will sit outside the frame.
 */
export function fit(svg, {height, minWidth = 260} = {}){
  const width = Math.max(minWidth, Math.round(svg.getBoundingClientRect().width) || minWidth);
  const h = height || Math.round(Number(svg.dataset.height) || 220);
  svg.setAttribute("viewBox", `0 0 ${width} ${h}`);
  svg.setAttribute("height", String(h));
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  return {W: width, H: h};
}

/** One line of SVG text. Monospace and a secondary colour by default — every tick label
 *  on every chart goes through this, which is what keeps them consistent.
 *
 *  The default size is the design plan's floor for a chart label (section 12), and it is
 *  a real 11 px only because `fit` has removed the scaling. */
export const txt = (x, y, s, o = {}) => `<text x="${x}" y="${y}" fill="${o.fill || 'var(--text-muted)'}"
  font-size="${o.size || 11}"
  font-family="${o.mono !== false ? 'var(--mono)' : 'var(--sans)'}"
  text-anchor="${o.anchor || 'start'}"${o.weight ? ` font-weight="${o.weight}"` : ''}>${s}</text>`;

/** When a chart cannot be drawn, say why — a blank chart looks exactly like "there was
 *  never any data here". */
export function blankChart(svg, why){
  if(!svg) return;
  const vb = (svg.getAttribute("viewBox") || "0 0 520 200").split(" ").map(Number);
  svg.innerHTML = `<text x="${vb[2] / 2}" y="${vb[3] / 2}" fill="var(--text-muted)" font-size="12"
    font-family="var(--sans)" text-anchor="middle">${why}</text>`;
  notice(why);
}
