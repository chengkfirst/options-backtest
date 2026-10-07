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

/** Columns that are an outcome or an identifier, so grouping the result by them would
 *  be grouping the answer by itself. */
export const EXCLUDE = new Set([
  "trade_id", "gross_pnl", "fees", "net_pnl", "entry_sig_close",
  "entry_sig_sma_20", "entry_sig_sma_50", "entry_sig_sma_200",
]);

/** Whether a value is "not computed" rather than a number. */
export const absent = v =>
  v == null || (typeof v === "number" && !Number.isFinite(v));

export const money = v => absent(v)
  ? "—"
  : (v >= 0 ? "+" : "−") + Math.abs(v).toLocaleString("en-US", {maximumFractionDigits: 0});

/** One minus sign across the interface. `money` has always written U+2212 and these
 *  wrote JavaScript's own hyphen, so a single band of cards showed "−31,220" beside
 *  "-60.6%" — two glyphs of different widths for the same idea, which in a monospace
 *  column also stops the digits lining up. */
const MINUS = (text) => text.replace("-", "\u2212");

export const pct = v => absent(v) ? "—" : MINUS((v * 100).toFixed(1)) + "%";

/** A size with no sign, for a sentence that has already said the direction in words.
 *  "Lost +31,220" is what happens when a signed formatter is handed an absolute value. */
export const size = v => absent(v)
  ? "—" : Math.abs(v).toLocaleString("en-US", {maximumFractionDigits: 0});

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

export const sgn = v => absent(v) ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "";

export const label = c => LABEL[c] || c;

export function fmtVal(col, v){
  if(absent(v)) return "—";
  if(PCT.has(col)) return (v * 100).toFixed(1) + "%";
  if(Math.abs(v) >= 100) return v.toFixed(0);
  if(Math.abs(v) >= 1) return v.toFixed(2);
  return v.toFixed(3);
}
