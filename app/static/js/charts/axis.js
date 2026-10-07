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

/* ── the hatch ───────────────────────────────────────────────────────────────────
 *
 * The design plan asks for the locked hold-out, and every span with no data, to be
 * **hatched** rather than filled: a flat tint reads as a region that has a value in it, and
 * that is the one thing these two spans do not have. `--holdout-fill` is the token for it
 * and had no user — a CSS gradient cannot fill an SVG shape — so the pattern is defined in
 * the document and referenced by the rects.
 */

/** The id of one layer's hatch pattern. An id resolves per document and each layer owns its
 *  own svg, so the id carries the layer's key. */
const hatchId = key => `hatch-${String(key).replace(/[^\w-]/g, "-")}`;

/** The 45° hatch the design plan asks for over the locked hold-out and over any span with
 *  no data. Defined per svg because that is the scope an id resolves in. */
const hatchPattern = key => `<defs><pattern id="${hatchId(key)}" width="8" height="8"
    patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
  <line class="hatch-line" x1="0" y1="0" x2="0" y2="8"/>
</pattern></defs>`;

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
          ${layer.columns ? `<button type="button" class="layer-csv" data-csv="${layer.key}"
             data-tip="Download this layer as CSV${state.brush
               ? ", for the brushed range" : ""}. The stored values, not the drawn ones.">CSV</button>` : ""}
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
      svg.innerHTML = hatchPattern(layer.key) + shading(scale, layer.key)
        + (layer.draw(scale, layer.key) || "") + crosshair(scale);
    }
    paintTip();
  }

  /** The hold-out and the brush, drawn under every layer so the eye reads one band. */
  function shading(scale, key){
    let out = "";
    // One trade's holding period, shaded across every layer at once. The design plan's
    // rule 4: selecting a trade shades the time it was open, so the question "what was the
    // book doing while this position was on" is answered by looking rather than by
    // remembering two dates.
    if(state.held){
      const a = indexOfDate(state.held.from), b = indexOfDate(state.held.to);
      // `scale.plot` carries edges, not a height — this read `scale.plot.height`, which is
      // undefined, so the rect went out as `height="undefined"` and SVG drew nothing. The
      // selftest asserted the element existed, which it did; a band of zero height is in
      // the document and invisible, so "selecting a trade shades its holding period" was
      // true of the markup and false of the screen. Height is measured here, and the test
      // now asserts the measurement.
      if(a != null && b != null)
        out += `<rect class="held" x="${scale.x(a)}" y="${scale.plot.top}"
                  width="${Math.max(scale.x(b) - scale.x(a), 1.5)}"
                  height="${scale.plot.bottom - scale.plot.top}"/>`;
    }

    if(state.holdOut){
      const a = indexOfDate(state.holdOut.from), b = indexOfDate(state.holdOut.to);
      if(a != null && b != null && b > a)
        out += `<rect class="holdout" x="${scale.x(a)}" y="${scale.plot.top}"
                  width="${scale.x(b) - scale.x(a)}"
                  height="${scale.plot.bottom - scale.plot.top}"
                  fill="url(#${hatchId(key)})"/>`;
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
          // A layer holding several series gives one row per series, **named**. Joined into
          // one string they read as three numbers in an order the reader has to remember —
          // and a comparison whose tooltip cannot say which run a number belongs to is worse
          // than no tooltip.
          if(layer.rows){
            return layer.rows(at).map(({name, value}) =>
              value === "" || value == null ? "" : `<div class="tip-row">
                <span class="tip-key">${esc(name)}</span>
                <span class="tip-val">${esc(value)}</span></div>`).join("");
          }
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

  /* ── taking a layer away with you ──────────────────────────────────────────────
   *
   * The design plan's chart rules end with "each layer exports PNG, CSV, or a link to the
   * current range". CSV, because the other two answer a smaller question: a researcher who
   * wants this layer outside the page wants to put it beside something else, and a picture
   * cannot be joined to anything.
   *
   * **The stored values, not the drawn ones.** Downsampling is a drawing step; a file that
   * carried the drawn points would be a different series from the one the metrics were
   * computed on, and would not say so. When a range is brushed the file is that range,
   * because that is what is on screen and what the reader means by "this".
   */
  function csv(layer){
    const from = state.brush ? state.brush.from : 0;
    const to = state.brush ? state.brush.to : state.dates.length - 1;
    const cols = layer.columns || [];
    const head = ["date", ...cols.map(c => c.name)];
    const lines = [head.map(quote).join(",")];
    for(let i = from; i <= to; i++)
      lines.push([String(state.dates[i]).slice(0, 10),
                  ...cols.map(c => {
                    const v = c.values[i];
                    return v == null || !Number.isFinite(v) ? "" : String(v);
                  })].map(quote).join(","));
    return lines.join("\n") + "\n";
  }

  /** A field that contains a comma, a quote or a newline would otherwise split the row. */
  function quote(value){
    const s = String(value ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  function download(name, text){
    const url = URL.createObjectURL(new Blob([text], {type: "text/csv;charset=utf-8"}));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    // Revoked on the next turn rather than immediately: Safari starts the download
    // asynchronously and a URL revoked in the same tick is gone before it is read.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  body.addEventListener("click", e => {
    const save = e.target.closest("[data-csv]");
    if(save){
      const layer = state.layers.find(l => l.key === save.dataset.csv);
      if(layer){
        const span = state.brush
          ? `${state.dates[state.brush.from]}_${state.dates[state.brush.to]}` : "full";
        download(`${layer.key}_${span}.csv`.replace(/[^\w.-]/g, "-"), csv(layer));
      }
      return;
    }
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

/** A line, optionally with a second comparison line and an area to a baseline.
 *
 * `log` draws the vertical axis on a logarithmic scale, which the design plan asks for on
 * the equity curve. It is **ignored where it would lie**: a log axis cannot show a value at
 * or below zero, and an account that went to zero is exactly the run whose curve must not
 * be quietly redrawn as if it had not. `logUsable` says which happened, so the control that
 * asked for it can report back instead of looking broken.
 */
export function lineLayer({key, title, hint, height, values, second, secondTitle,
                           baseline, colour = "var(--data-strategy)", format, log = false}){
  const finite = values.filter(v => v != null && Number.isFinite(v));
  const others = (second || []).filter(v => v != null && Number.isFinite(v));
  const logUsable = log && finite.every(v => v > 0) && others.every(v => v > 0)
    && finite.length > 0;
  const scaleOf = v => logUsable ? Math.log10(v) : v;
  const lo = Math.min(...finite.map(scaleOf), ...others.map(scaleOf));
  const hi = Math.max(...finite.map(scaleOf), ...others.map(scaleOf));
  return {
    key, title, hint, height, logUsable,
    columns: [{name: title, values},
              ...(second ? [{name: secondTitle || "second", values: second}] : [])],
    read: i => format ? format(values[i]) : String(values[i] ?? ""),
    draw(scale){
      if(!finite.length) return "";
      const y = raw => scale.plot.bottom
        - ((scaleOf(raw) - lo) / ((hi - lo) || 1)) * (scale.plot.bottom - scale.plot.top);
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
      const inside = v => scaleOf(v) >= lo && scaleOf(v) <= hi;
      if(baseline != null && Number.isFinite(baseline) && (!logUsable || baseline > 0)
         && inside(baseline))
        out += `<line class="rule" x1="${scale.plot.left}" x2="${scale.plot.right}"
                  y1="${y(baseline)}" y2="${y(baseline)}"/>`;
      if(second)
        out += `<path class="series second" d="${path(second)}" fill="none"
                  stroke="var(--data-benchmark)"><title>${esc(secondTitle || "")}</title></path>`;
      out += `<path class="series" d="${path(values)}" fill="none" stroke="${colour}"/>`;
      // The labels carry the **values**, never the transform: on a log axis `hi` is a
      // logarithm, and printing it would put "5.1" on an axis of dollars.
      const rawHi = Math.max(...finite, ...others);
      const rawLo = Math.min(...finite, ...others);
      for(const v of [rawHi, rawLo])
        out += `<text class="y-label" x="${scale.plot.left - 6}" y="${y(v) + 3.5}"
                  text-anchor="end">${format ? format(v) : v.toFixed(0)}</text>`;
      return out;
    },
  };
}

/** Several series on one scale — the comparison's overlay, one line per run.
 *
 * **Colour is not the only carrier.** Each series takes its colour from `--data-N` and its
 * dash pattern from `--dash-N`, in column order, because a line has no number printed on it:
 * under either common form of colour blindness two of the four proposed series are nearly the
 * same hue, and a comparison where two runs are drawn alike is worse than no comparison. The
 * dash survives colour blindness, greyscale printing and a washed-out projector.
 *
 * A series with no value on a date breaks rather than spanning it, so a run that covered a
 * shorter period does not get a straight line drawn across the years it did not run.
 */
export function seriesLayer({key, title, hint, height, series, format}){
  const all = series.flatMap(s => s.values.filter(v => v != null && Number.isFinite(v)));
  const lo = Math.min(...all), hi = Math.max(...all);
  return {
    key, title, hint, height,
    columns: series.map(s => ({name: s.name, values: s.values})),
    // Every series at the hovered date, **one named row each**: the fixed tooltip exists so
    // one date can be read across everything on screen, and that only works if each number
    // carries the name of what it belongs to.
    rows: i => series.map(s => {
      const v = s.values[i];
      return {name: s.name,
              value: v == null || !Number.isFinite(v) ? "—" : (format ? format(v) : String(v))};
    }),
    // The layer's own bar shows the first series, which is the baseline column.
    read: i => {
      const v = series[0]?.values[i];
      return v == null || !Number.isFinite(v) ? "" : (format ? format(v) : String(v));
    },
    draw(scale){
      if(!all.length) return "";
      const y = v => scale.plot.bottom
        - ((v - lo) / ((hi - lo) || 1)) * (scale.plot.bottom - scale.plot.top);
      let out = "";
      for(const s of series){
        let d = "", pen = false;
        s.values.forEach((v, i) => {
          if(v == null || !Number.isFinite(v)){ pen = false; return; }
          d += `${pen ? "L" : "M"}${scale.x(i).toFixed(1)},${y(v).toFixed(1)}`;
          pen = true;
        });
        // `style` and not a presentation attribute: a custom property is substituted in a
        // declaration and **not** in an attribute value, so `stroke="var(--data-2)"` would
        // leave the line with no colour at all — and a line that is simply not there is the
        // hardest kind of missing thing to notice on a chart with three others on it.
        out += `<path class="series" d="${d}" fill="none"
          style="stroke:var(--data-${s.column});stroke-dasharray:var(--dash-${s.column})"
          ><title>${esc(s.name)}</title></path>`;
      }
      for(const v of [hi, lo])
        out += `<text class="y-label" x="${scale.plot.left - 6}" y="${y(v) + 3.5}"
                  text-anchor="end">${format ? format(v) : v.toFixed(0)}</text>`;
      return out;
    },
  };
}

/** A run of consecutive indices that have a value, so an area can be drawn per run and a
 *  day with no value stays a hole rather than being filled with a zero.
 *
 * Substituting zero is the quiet version of interpolating: a missing drawdown became "at
 * its peak that day" and a missing deployed figure became "nothing on the book", both of
 * which the eye reads as a fact about the strategy. The design plan's rule is that gaps are
 * hatched and never crossed; this is the half of it that keeps the area honest. */
function runsOf(values){
  const out = [];
  let run = null;
  values.forEach((v, i) => {
    if(v == null || !Number.isFinite(v)){ run = null; return; }
    if(!run){ run = {from: i, points: []}; out.push(run); }
    run.points.push([i, v]);
  });
  return out;
}

/** The hatch over a span with no value, so a hole reads as a hole and not as a zero. */
function gapHatch(values, scale, key){
  let out = "";
  for(let i = 0; i < values.length; i++){
    const v = values[i];
    if(v != null && Number.isFinite(v)) continue;
    let j = i;
    while(j + 1 < values.length
          && (values[j + 1] == null || !Number.isFinite(values[j + 1]))) j++;
    out += `<rect class="gap" x="${scale.x(i)}" y="${scale.plot.top}"
              width="${Math.max(scale.x(j) - scale.x(i), 1.5)}"
              height="${scale.plot.bottom - scale.plot.top}"
              fill="url(#${hatchId(key)})"/>`;
    i = j;
  }
  return out;
}

/** An area hanging from zero — a drawdown, which is always negative. */
export function underwaterLayer({key, title, hint, height, values, format}){
  const finite = values.filter(v => v != null && Number.isFinite(v));
  const worst = Math.min(...finite, 0);
  return {
    key, title, hint, height,
    columns: [{name: title, values}],
    read: i => format ? format(values[i]) : String(values[i] ?? ""),
    draw(scale, key){
      if(!finite.length) return "";
      const y = v => scale.plot.top
        + (v / (worst || -1)) * (scale.plot.bottom - scale.plot.top);
      const areas = runsOf(values).map(run => {
        const [first] = run.points;
        let d = `M${scale.x(first[0]).toFixed(1)},${scale.plot.top}`;
        for(const [i, v] of run.points) d += `L${scale.x(i).toFixed(1)},${y(v).toFixed(1)}`;
        const last = run.points[run.points.length - 1];
        return `<path class="underwater" d="${d}L${scale.x(last[0]).toFixed(1)},${
          scale.plot.top}Z"/>`;
      }).join("");
      return gapHatch(values, scale, key) + areas
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
    columns: [{name: title, values}],
    read: i => format ? format(values[i]) : String(values[i] ?? ""),
    draw(scale, key){
      if(!finite.length) return "";
      const y = v => scale.plot.bottom
        - (v / (hi || 1)) * (scale.plot.bottom - scale.plot.top);
      let out = gapHatch(values, scale, key) + runsOf(values).map(run => {
        const [first] = run.points;
        let d = `M${scale.x(first[0]).toFixed(1)},${scale.plot.bottom}`;
        for(const [i, v] of run.points) d += `L${scale.x(i).toFixed(1)},${y(v).toFixed(1)}`;
        const last = run.points[run.points.length - 1];
        return `<path class="area" d="${d}L${scale.x(last[0]).toFixed(1)},${
          scale.plot.bottom}Z" fill="${colour}"/>`;
      }).join("");
      if(limit)
        out += `<line class="limit" x1="${scale.plot.left}" x2="${scale.plot.right}"
                  y1="${y(limit)}" y2="${y(limit)}"/>`
             + `<text class="y-label" x="${scale.plot.left - 6}" y="${y(limit) + 3.5}"
                  text-anchor="end">${format ? format(limit) : limit}</text>`;
      return out;
    },
  };
}
