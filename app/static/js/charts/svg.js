/* charts/svg.js — the helpers every chart emits through.
 *
 * Here rather than in fmt.js because these emit SVG markup; fmt.js stays pure text.
 * Moved out of the single charts.js with the bodies unchanged.
 */
"use strict";

import {notice} from "../net.js";

/** One line of SVG text. Monospace and a secondary colour by default — every tick label
 *  on every chart goes through this, which is what keeps them consistent. */
export const txt = (x, y, s, o = {}) => `<text x="${x}" y="${y}" fill="${o.fill || 'var(--text-muted)'}"
  font-size="${o.size || 10}"
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
