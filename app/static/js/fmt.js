/* fmt.js — turning numbers and column names into something a person reads.
 *
 * Formatting only: there is no DOM reference and no statistic in this file, so it can be
 * exercised on its own and from the states gallery.
 *
 * The one rule here is about absence. The backend sends `null` for "this cannot be
 * computed" — `_finite` in runner.py converts every NaN and infinity to null for exactly
 * that reason — and absence must never print as a number. `pct(null)` once multiplied
 * null by 100 and rendered **0.0%**, inventing the figure the conversion existed to
 * prevent. A real zero still prints as zero; that distinction is the whole point.
 */
"use strict";

export const LABEL = {
  entry_iv: "IV at entry", entry_delta: "delta at entry",
  entry_moneyness: "moneyness at entry", entry_dte_calendar: "days to expiry at entry",
  entry_underlying_price: "spot at entry", entry_price: "premium paid",
  exit_price: "premium received", holding_days: "trading days held",
  entry_sig_rsi_14: "RSI at entry", entry_sig_rv_20: "realised vol at entry",
  entry_sig_mom_20: "20-day momentum at entry", entry_sig_atr_14: "ATR at entry",
  entry_sig_bb_width_20: "Bollinger width at entry", ma_gap: "price vs the 20-day",
  trend: "price vs the 200-day", exit_iv: "IV at exit", exit_delta: "delta at exit",
};

/** Columns whose unit is a fraction and which therefore read as a percentage. */
export const PCT = new Set([
  "entry_moneyness", "ma_gap", "trend", "entry_sig_mom_20", "entry_iv", "exit_iv",
  "entry_sig_rv_20", "entry_sig_bb_width_20",
]);

export const REASON = {
  take_profit: "take profit", stop_loss: "stop loss", time_exit: "holding cap",
  dte_floor: "days-to-expiry floor", expiry_itm: "expired in the money",
  expiry_otm: "expired out of the money", risk_forced: "forced by risk",
};

/** The seven slots, in the architecture's order and words. The number travels with the name
 *  wherever a slot is drawn: section 12 asks that every slot marker carry its number, so
 *  colour is never the only thing telling two slots apart. */
export const SLOT = {
  1: "setup / regime", 2: "entry", 3: "take profit", 4: "stop loss",
  5: "headwind", 6: "positioning", 7: "hedge / protection",
};

/** Which slot closed a trade, by the reason the engine recorded — and `null` where the
 *  answer is honestly "no slot did".
 *
 * Three of the reasons are not a slot: a holding cap and a days-to-expiry floor are
 * lifecycle settings, and an expiry is the contract ending. Mapping them onto a slot anyway
 * would put a colour and a number on a decision no component made, and the attribution by
 * closing slot would then credit a component for what the calendar did. */
export const SLOT_OF_EXIT = {
  take_profit: 3, stop_loss: 4, risk_forced: 5,
  time_exit: null, dte_floor: null, expiry_itm: null, expiry_otm: null,
};

/** Columns that are an outcome or an identifier, so grouping the result by them would
 *  be grouping the answer by itself. */
export const EXCLUDE = new Set([
  "trade_id", "gross_pnl", "fees", "net_pnl", "entry_sig_close",
  "entry_sig_sma_20", "entry_sig_sma_50", "entry_sig_sma_200",
]);

/** Whether a value is "not computed" rather than a number. */
export const absent = v =>
  v == null || (typeof v === "number" && !Number.isFinite(v));

/** The magnitude, at the precision the size calls for: whole dollars at a thousand and
 *  above, cents below. At four figures the cents are noise; at two they are the number,
 *  and a P&L of 3.15 printed as "+3" was rounding away most of it. */
const mag = v => {
  const n = Math.abs(v);
  return n >= 1000
    ? n.toLocaleString("en-US", {maximumFractionDigits: 0})
    : n.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
};

/** Money, written one way everywhere: a dollar sign, thousands separators, the sign
 *  always shown.
 *
 * The same rules say units belong in a column header rather than in every cell, which is
 * the opposite instruction inside a table. Both hold: `money` is for prose, cards and
 * sentences, and `bare` is for a column whose header already names the unit. */
export const money = v => absent(v) ? "—" : (v >= 0 ? "+" : "−") + "$" + mag(v);

/** The same number without its unit, for a column headed "net P&L ($)". */
export const bare = v => absent(v) ? "—" : (v >= 0 ? "+" : "−") + mag(v);

/** One minus sign across the interface. `money` has always written U+2212 and these
 *  wrote JavaScript's own hyphen, so a single band of cards showed "−31,220" beside
 *  "-60.6%" — two glyphs of different widths for the same idea, which in a monospace
 *  column also stops the digits lining up. */
const MINUS = (text) => text.replace("-", "\u2212");

/** A percentage at one decimal, and **two below one percent**, where one decimal rounds a
 *  0.35% cost to 0.4% — a sixth of it, lost to the format. */
export const pct = v => {
  if(absent(v)) return "—";
  const n = v * 100;
  return MINUS(n.toFixed(Math.abs(n) < 1 && n !== 0 ? 2 : 1)) + "%";
};

/** Unsigned money, for a sentence that has already said the direction in words — "lost
 *  $31,220". The unit stays: the sign is what the sentence supplied, not the currency.
 *  "Lost +31,220" is what happens when a signed formatter is handed an absolute value. */
export const size = v => absent(v) ? "—" : "$" + mag(v);

export const num = (v, digits = 2) => absent(v) ? "—" : MINUS(v.toFixed(digits));

/** Money in as few characters as carry the magnitude: −23.3k, +1.19M.
 *
 * For places where the full figure does not fit and being cut off mid-number is worse
 * than being rounded — an interval under a metric card showed "+57,18…", which reads as a
 * different number from the one it is. The exact value stays one hover away. */
export const moneyShort = v => {
  if(absent(v)) return "—";
  const sign = v >= 0 ? "+" : "−";
  const size = Math.abs(v);
  if(size >= 1e6) return `${sign}${(size / 1e6).toFixed(2)}M`;
  if(size >= 1e3) return `${sign}${(size / 1e3).toFixed(1)}k`;
  return `${sign}${size.toFixed(0)}`;
};

/* ── dates, written for where they are read ──────────────────────────────────────
 *
 * Three forms, because the same date serves three purposes. A table, an identifier and a
 * URL take ISO, which sorts and round-trips. Prose takes a month name, because "2023-02-17"
 * inside a sentence reads as a serial number. A chart axis takes the shortest form that
 * stays unambiguous, because the year is already said by the axis.
 *
 * Parsed as UTC. A bare `new Date("2023-02-17")` is UTC midnight and
 * `toLocaleDateString` then renders it in the local zone — which is the previous day
 * anywhere west of Greenwich, so a trade opened on the 17th was labelled the 16th.
 */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const parts = iso => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso ?? ""));
  return m ? {y: +m[1], mo: +m[2], d: +m[3]} : null;
};

/** `Oct 7, 2026` — for prose and tooltips. */
export const dateLong = iso => {
  const p = parts(iso);
  return p ? `${MONTHS[p.mo - 1]} ${p.d}, ${p.y}` : "—";
};

/** `Oct 7` — for a chart axis, where the year is already established. */
export const dateShort = iso => {
  const p = parts(iso);
  return p ? `${MONTHS[p.mo - 1]} ${p.d}` : "—";
};

/** An expiry says the date and how far away it is together, because neither alone answers
 *  "is this the near month". */
export const expiry = (iso, dte) =>
  absent(dte) ? dateLong(iso) : `${iso} · ${Math.round(dte)} DTE`;

export const sgn = v => absent(v) ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";

/** A column's name, written for a reader.
 *
 * The table above names the ones worth a phrase of their own. Everything else the engine
 * writes at entry follows one pattern — `entry_sig_<signal>` — so the fallback unfolds it
 * rather than printing the identifier: a trade card listing `entry_sig_iv_rank_252` asks the
 * reader to know the storage format, and the whole point of the context block is that they
 * can read what the slots saw. An unrecognised name still prints as itself, which is the
 * honest outcome for a column nothing here knows about. */
export const label = c => {
  if(LABEL[c]) return LABEL[c];
  const signal = /^entry_sig_(.+)$/.exec(String(c ?? ""));
  return signal ? `${signal[1].replace(/_/g, " ")} at entry` : c;
};

export function fmtVal(col, v){
  if(absent(v)) return "—";
  if(PCT.has(col)) return (v * 100).toFixed(1) + "%";
  if(Math.abs(v) >= 100) return v.toFixed(0);
  if(Math.abs(v) >= 1) return v.toFixed(2);
  return v.toFixed(3);
}
