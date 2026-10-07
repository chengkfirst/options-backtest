/* charts/axis.js — the chart stack: several layers, one time axis.
 *
 * The design plan's section 13 states the rules, and they are the reason this exists
 * rather than another chart function:
 *
 *   - all layers share the x axis, the time brush and the crosshair
 *   - y axes are independent, with their labels inside the plot
 *   - each layer collapses to a title bar
 *   - the tooltip is fixed at the top right and lists **every** layer's value at the
 *     hovered time, so reading one date across four quantities needs no eye travel
 *   - brushing filters every chart and the trade table
 *   - missing data is hatched, never interpolated across
 *
 * A layer draws itself into a scale it is given. It does not know where it is on the
 * page, how wide it is, or what else is on screen — which is what lets the same layer
 * appear in the overview and in a comparison without being rewritten.
 *
 * Unlike the older render functions, nothing here writes to a fixed element id: the group
 * is handed the node it owns. That was a real limit, not a stylistic one — the states
 * gallery could not show one chart twice because the second render overwrote the first.
 */
"use strict";

import {esc} from "../dom.js";
import {dateLong, dateShort} from "../fmt.js";

const PAD = {left: 58, right: 14, top: 10, bottom: 4};
const AXIS_HEIGHT = 20;

/**
 * Build a stack.
 *
 * @param {HTMLElement} root  the node the group owns and fills
 * @param {object} spec
 *   dates   {string[]}  the shared domain, one entry per step, ascending
 *   layers  {object[]}  {key, title, height, hint, series, draw, read}
 *   holdOut {object}    {from, to} drawn as a hatch and left empty, or null
 *   held    {object}    {from, to} of one trade's holding period, shaded, or null
 *   marks   {object[]}  {date, kind, label} drawn as triangles on the axis
 *   onBrush {function}  called with ({from, to}) or (null) when cleared
 */
export function chartStack(root, spec){
  const state = {
    dates: spec.dates || [],
    layers: (spec.layers || []).filter(Boolean),
    holdOut: spec.holdOut || null,
    held: spec.held || null,
    marks: spec.marks || [],
    onBrush: spec.onBrush || (() => {}),
    collapsed: new Set(spec.collapsed || []),
    brush: spec.brush || null,     // {from, to} as indices
    hover: null,
  };
  root.classList.add("stack");
  root.innerHTML = "";

  const body = document.createElement("div");
  body.className = "stack-body";
  const tip = document.createElement("div");
  tip.className = "stack-tip";
  tip.hidden = true;
  root.append(body, tip);

  function width(){
    // The group is laid out by its container; measuring once per draw keeps the SVG
    // coordinates and the pointer coordinates in the same space, which is what makes the
    // crosshair land on the value it names.
    return Math.max(360, root.clientWidth || 900);
  }

  const xOf = (i, w) => PAD.left + (state.dates.length < 2 ? 0
    : (i / (state.dates.length - 1)) * (w - PAD.left - PAD.right));
  const indexAt = (px, w) => {
    if(state.dates.length < 2) return 0;
    const t = (px - PAD.left) / (w - PAD.left - PAD.right);
    return Math.max(0, Math.min(state.dates.length - 1, Math.round(t * (state.dates.length - 1))));
  };

  function draw(){
    const w = width();
    body.innerHTML = state.layers.map(layer => {
      const open = !state.collapsed.has(layer.key);
      const h = open ? (layer.height || 120) : 0;
      return `<section class="layer-chart" data-layer="${layer.key}">
        <header class="layer-bar">
          <button type="button" class="layer-toggle" data-toggle="${layer.key}"
                  aria-expanded="${open}">${open ? "▾" : "▸"}</button>
          <span class="layer-title">${esc(layer.title)}</span>
          ${layer.hint ? `<span class="layer-hint">${esc(layer.hint)}</span>` : ""}
          <span class="layer-value" data-value="${layer.key}"></span>
        </header>
        ${open ? `<svg class="layer-svg" viewBox="0 0 ${w} ${h}"
                      preserveAspectRatio="none" height="${h}"
                      data-svg="${layer.key}"></svg>` : ""}
      </section>`;
    }).join("") + axisStrip(w);

    for(const layer of state.layers){
      if(state.collapsed.has(layer.key)) continue;
      const svg = body.querySelector(`[data-svg="${CSS.escape(layer.key)}"]`);
      if(!svg) continue;
      const h = layer.height || 120;
      const scale = {
        w, h,
        x: i => xOf(i, w),
        plot: {top: PAD.top, bottom: h - PAD.bottom,
               left: PAD.left, right: w - PAD.right},
        dates: state.dates,
      };
      svg.innerHTML = shading(scale) + (layer.draw(scale) || "") + crosshair(scale);
    }
    paintTip();
  }

  /** The hold-out and the brush, drawn under every layer so the eye reads one band. */
  function shading(scale){
    let out = "";
    // One trade's holding period, shaded across every layer at once. The design plan's
    // rule 4: selecting a trade shades the time it was open, so the question "what was the
    // book doing while this position was on" is answered by looking rather than by
    // remembering two dates.
    if(state.held){
      const a = indexOfDate(state.held.from), b = indexOfDate(state.held.to);
      if(a != null && b != null)
        out += `<rect class="held" x="${scale.x(a)}" y="${scale.plot.top}"
                  width="${Math.max(scale.x(b) - scale.x(a), 1.5)}"
                  height="${scale.plot.height}"/>`;
    }

    if(state.holdOut){
      const a = indexOfDate(state.holdOut.from), b = indexOfDate(state.holdOut.to);
      if(a != null && b != null && b > a)
        out += `<rect class="holdout" x="${scale.x(a)}" y="${scale.plot.top}"
                  width="${scale.x(b) - scale.x(a)}"
                  height="${scale.plot.bottom - scale.plot.top}"/>`;
    }
    if(state.brush){
      const a = scale.x(state.brush.from), b = scale.x(state.brush.to);
      out += `<rect class="brushed" x="${Math.min(a, b)}" y="${scale.plot.top}"
                width="${Math.abs(b - a)}" height="${scale.plot.bottom - scale.plot.top}"/>`;
    }
    return out;
  }

  /** The crosshair is always in the document and moved on hover, never created by it.
   *  Drawn only when hovering, it did not exist for the pointer handler to move, so the
   *  line never appeared while the tooltip updated — the value was readable and the place
   *  it came from was not. */
  function crosshair(scale){
    const x = scale.x(state.hover ?? 0);
    return `<line class="cross" x1="${x}" x2="${x}" y1="${scale.plot.top}"
              y2="${scale.plot.bottom}"${state.hover == null ? ' opacity="0"' : ""}/>`;
  }

  /** The date axis, shared, plus the event markers. */
  /** The month alone, for an axis that has already said the year. */
const MONTH_OF = iso => dateShort(iso).split(" ")[0];

function axisStrip(w){
    const n = state.dates.length;
    if(!n) return "";
    // Ticks at the grain the span can carry. It used to tick only where the year changed,
    // so a run inside one year got a single label and a lone `2023-04` bolted on the end —
    // an axis with one mark is a caption. Under two years the months are the useful
    // divisions; beyond that the years are.
    const first = String(state.dates[0]).slice(0, 10);
    const last = String(state.dates[n - 1]).slice(0, 10);
    const months = (+last.slice(0, 4) - +first.slice(0, 4)) * 12
                 + (+last.slice(5, 7) - +first.slice(5, 7));
    const ticks = [];
    if(months <= 24){
      let lastKey = "";
      for(let i = 0; i < n; i++){
        const d = String(state.dates[i]);
        const key = d.slice(0, 7);
        if(key === lastKey) continue;
        lastKey = key;
        // The month, and the year with it the first time and whenever it turns over, so
        // the axis never asks the reader to remember which year they are in.
        const showYear = ticks.length === 0 || d.slice(0, 4) !== String(state.dates[ticks[ticks.length - 1].i]).slice(0, 4);
        ticks.push({i, label: showYear ? `${dateShort(d)}` : MONTH_OF(d)});
      }
    }else{
      let lastYear = "";
      for(let i = 0; i < n; i++){
        const year = String(state.dates[i]).slice(0, 4);
        if(year !== lastYear){ ticks.push({i, label: year}); lastYear = year; }
      }
    }
    const marks = state.marks.map(m => {
      const i = indexOfDate(m.date);
      if(i == null) return "";
      return `<polygon class="mark ${m.kind}" title="${esc(m.label || m.kind)}"
        points="${xOf(i, w) - 4},6 ${xOf(i, w) + 4},6 ${xOf(i, w)},0"><title>${
        esc(m.label || m.kind)}</title></polygon>`;
    }).join("");
    return `<svg class="stack-axis" viewBox="0 0 ${w} ${AXIS_HEIGHT}" height="${AXIS_HEIGHT}"
                 preserveAspectRatio="none">
      ${marks}
      ${ticks.map(t => `<text class="tick" x="${xOf(t.i, w)}" y="${AXIS_HEIGHT - 6}"
         >${t.label}</text>`).join("")}
    </svg>`;
  }

  function indexOfDate(date){
    if(!date) return null;
    const want = String(date).slice(0, 10);
    // The domain is ascending, so the first entry not before `want` is the one. A date
    // outside the range clamps rather than vanishing: a hold-out that starts before the
    // run still shades the part that overlaps it.
    for(let i = 0; i < state.dates.length; i++)
      if(String(state.dates[i]).slice(0, 10) >= want) return i;
    return state.dates.length ? state.dates.length - 1 : null;
  }

  /** The fixed tooltip: every layer's value at the hovered date, in one place. */
  function paintTip(){
    if(state.hover == null || !state.dates.length){
      tip.hidden = true;
      for(const el of body.querySelectorAll("[data-value]")) el.textContent = "";
      return;
    }
    const at = state.hover;
    tip.hidden = false;
    tip.innerHTML = `<div class="tip-date">${esc(dateLong(state.dates[at]))}</div>`
      + state.layers.map(layer => {
          const value = layer.read ? layer.read(at) : "";
          return value === "" || value == null ? "" : `<div class="tip-row">
            <span class="tip-key">${esc(layer.title)}</span>
            <span class="tip-val">${esc(value)}</span></div>`;
        }).join("");
    // Each layer's own bar repeats its value, so a collapsed layer still answers.
    for(const layer of state.layers){
      const slot = body.querySelector(`[data-value="${CSS.escape(layer.key)}"]`);
      if(slot) slot.textContent = layer.read ? (layer.read(at) ?? "") : "";
    }
  }

  /* ── pointer: one overlay for the whole stack ── */
  let dragFrom = null;

  body.addEventListener("pointermove", e => {
    const svg = e.target.closest(".layer-svg, .stack-axis");
    if(!svg) return;
    const rect = svg.getBoundingClientRect();
    const w = width();
    const px = (e.clientX - rect.left) / rect.width * w;
    state.hover = indexAt(px, w);
    if(dragFrom != null){
      state.brush = {from: Math.min(dragFrom, state.hover), to: Math.max(dragFrom, state.hover)};
      draw();
    }else{
      // Only the crosshair moved: redrawing every layer for a mouse move is wasteful, so
      // the lines are moved in place and the tooltip repainted.
      for(const layer of state.layers){
        const node = body.querySelector(`[data-svg="${CSS.escape(layer.key)}"] .cross`);
        if(node){
          const x = xOf(state.hover, w);
          node.setAttribute("x1", x);
          node.setAttribute("x2", x);
          node.removeAttribute("opacity");
        }
      }
      paintTip();
    }
  });

  body.addEventListener("pointerleave", () => {
    state.hover = null;
    paintTip();
  });

  body.addEventListener("pointerdown", e => {
    const svg = e.target.closest(".layer-svg, .stack-axis");
    if(!svg) return;
    const rect = svg.getBoundingClientRect();
    dragFrom = indexAt((e.clientX - rect.left) / rect.width * width(), width());
  });

  window.addEventListener("pointerup", () => {
    if(dragFrom == null) return;
    const was = dragFrom;
    dragFrom = null;
    // A click, not a drag: clear the brush rather than selecting one day, which no chart
    // can show and which would read as a bug.
    if(state.brush && state.brush.to - state.brush.from < 2){
      state.brush = null;
      draw();
      state.onBrush(null);
      return;
    }
    if(state.brush && was != null)
      state.onBrush({from: state.dates[state.brush.from], to: state.dates[state.brush.to]});
  });

  body.addEventListener("click", e => {
    const toggle = e.target.closest("[data-toggle]");
    if(!toggle) return;
    const key = toggle.dataset.toggle;
    if(state.collapsed.has(key)) state.collapsed.delete(key);
    else state.collapsed.add(key);
    draw();
  });

  // The container's **width** decides the coordinate space, so only a width change has to
  // redraw. Redrawing on any resize is a loop: `draw` rewrites the markup, which changes
  // the element's height, which notifies the observer, which draws again — visible as a
  // flicker whenever anything on the page moved, and worst while scrolling, when the
  // sticky header above was also changing size.
  let lastWidth = 0;
  let pending = 0;
  const observer = new ResizeObserver(entries => {
    const now = Math.round(entries[0].contentRect.width);
    if(now === lastWidth) return;
    lastWidth = now;
    // **Drawn on the next frame, not inside the callback.** Redrawing during delivery
    // changes layout while the browser is still handing out resize notifications, which
    // it reports as "ResizeObserver loop completed with undelivered notifications" — an
    // error the page's own strip then shows to the reader. Deferring takes the mutation
    // out of that window entirely.
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(draw);
  });
  observer.observe(root);

  draw();

  return {
    draw,
    /** Shade one trade's holding period, or clear it. */
    setHeld(range){
      state.held = range && range.from && range.to ? range : null;
      draw();
    },
    /** Set the brush from outside — restoring it from the URL, for instance. */
    setBrush(range){
      state.brush = range && range.from && range.to
        ? {from: indexOfDate(range.from), to: indexOfDate(range.to)}
        : null;
      draw();
    },
    destroy(){
      observer.disconnect();
      cancelAnimationFrame(pending);
    },
  };
}

/* ── the layer kinds ────────────────────────────────────────────────────────────
   Each returns a `draw` for the stack. They take values already computed by the
   backend; none of them derives a statistic. */

/** A line, optionally with a second comparison line and an area to a baseline. */
export function lineLayer({key, title, hint, height, values, second, secondTitle,
                           baseline, colour = "var(--data-strategy)", format}){
  const finite = values.filter(v => v != null && Number.isFinite(v));
  const lo = Math.min(...finite, ...(second || []).filter(Number.isFinite));
  const hi = Math.max(...finite, ...(second || []).filter(Number.isFinite));
  return {
    key, title, hint, height,
    read: i => format ? format(values[i]) : String(values[i] ?? ""),
    draw(scale){
      if(!finite.length) return "";
      const y = v => scale.plot.bottom
        - ((v - lo) / ((hi - lo) || 1)) * (scale.plot.bottom - scale.plot.top);
      // A gap is a gap: the path breaks rather than drawing a straight line across days
      // the run has no value for, which would invent a trend.
      const path = series => {
        let d = "", pen = false;
        series.forEach((v, i) => {
          if(v == null || !Number.isFinite(v)){ pen = false; return; }
          d += `${pen ? "L" : "M"}${scale.x(i).toFixed(1)},${y(v).toFixed(1)}`;
          pen = true;
        });
        return d;
      };
      let out = "";
      if(baseline != null && baseline >= lo && baseline <= hi)
        out += `<line class="rule" x1="${scale.plot.left}" x2="${scale.plot.right}"
                  y1="${y(baseline)}" y2="${y(baseline)}"/>`;
      if(second)
        out += `<path class="series second" d="${path(second)}" fill="none"
                  stroke="var(--data-benchmark)"><title>${esc(secondTitle || "")}</title></path>`;
      out += `<path class="series" d="${path(values)}" fill="none" stroke="${colour}"/>`;
      for(const v of [hi, lo])
        out += `<text class="y-label" x="${scale.plot.left - 6}" y="${y(v) + 3.5}"
                  text-anchor="end">${format ? format(v) : v.toFixed(0)}</text>`;
      return out;
    },
  };
}

/** An area hanging from zero — a drawdown, which is always negative. */
export function underwaterLayer({key, title, hint, height, values, format}){
  const finite = values.filter(v => v != null && Number.isFinite(v));
  const worst = Math.min(...finite, 0);
  return {
    key, title, hint, height,
    read: i => format ? format(values[i]) : String(values[i] ?? ""),
    draw(scale){
      if(!finite.length) return "";
      const y = v => scale.plot.top
        + (v / (worst || -1)) * (scale.plot.bottom - scale.plot.top);
      let d = `M${scale.x(0)},${scale.plot.top}`;
      values.forEach((v, i) => {
        d += `L${scale.x(i).toFixed(1)},${y(Number.isFinite(v) ? v : 0).toFixed(1)}`;
      });
      d += `L${scale.x(values.length - 1)},${scale.plot.top}Z`;
      return `<path class="underwater" d="${d}"/>`
        + `<text class="y-label" x="${scale.plot.left - 6}" y="${scale.plot.bottom}"
             text-anchor="end">${format ? format(worst) : worst.toFixed(2)}</text>`;
    },
  };
}

/** A step area with a limit line — premium at risk against the budget. */
export function areaLayer({key, title, hint, height, values, limit, format,
                           colour = "var(--leg-main)"}){
  const finite = values.filter(v => v != null && Number.isFinite(v));
  const hi = Math.max(...finite, limit || 0);
  return {
    key, title, hint, height,
    read: i => format ? format(values[i]) : String(values[i] ?? ""),
    draw(scale){
      if(!finite.length) return "";
      const y = v => scale.plot.bottom
        - (v / (hi || 1)) * (scale.plot.bottom - scale.plot.top);
      let d = `M${scale.x(0)},${scale.plot.bottom}`;
      values.forEach((v, i) => {
        d += `L${scale.x(i).toFixed(1)},${y(Number.isFinite(v) ? v : 0).toFixed(1)}`;
      });
      d += `L${scale.x(values.length - 1)},${scale.plot.bottom}Z`;
      let out = `<path class="area" d="${d}" fill="${colour}"/>`;
      if(limit)
        out += `<line class="limit" x1="${scale.plot.left}" x2="${scale.plot.right}"
                  y1="${y(limit)}" y2="${y(limit)}"/>`
             + `<text class="y-label" x="${scale.plot.left - 6}" y="${y(limit) + 3.5}"
                  text-anchor="end">${format ? format(limit) : limit}</text>`;
      return out;
    },
  };
}
