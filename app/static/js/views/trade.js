/* views/trade.js — one trade, read in the drawer beside the run it came from.
 *
 * The design plan's section 7. Its order is the order the questions arrive in: what the
 * trade was, what closed it and why, what it did while it was open, and what it was bought
 * on. The hero is the trade's P&L.
 *
 * **Everything here is a stored column of the trade log.** The card does not work out a
 * return, a ratio or an excursion: `mfe` and `mae` are the engine's own excursions in
 * premium terms, the P&L split is `gross_pnl`, `fees`, `net_pnl` as recorded, and the
 * context block is the `entry_sig_*` columns the engine wrote at entry. Two prices are shown
 * as two prices rather than as the move between them, which is the same rule applied to one
 * trade: the system can reproduce a column and cannot reproduce a figure this file invented.
 *
 * Four of the plan's blocks are **named and not drawn**, because the data for them is not in
 * the trade log: every action between the open and the close (ADD, REDUCE, ROLL, SET_STOP,
 * the HEDGEs), the price and P&L path over the hold, the shadow-tracking tail after the
 * exit, and the fill-to-order-to-signal trace. All four need the TradeBook and decision-log
 * endpoints of the plan's section 14. Saying so is the point: a card that quietly left them
 * out would read as a trade that had no actions.
 */
"use strict";

import {esc} from "../dom.js";
import {REASON, SLOT, SLOT_OF_EXIT, absent, dateLong, expiry, fmtVal, label, money, num,
        pct, size} from "../fmt.js";

/** The underlying and the contract, out of the engine's contract id.
 *
 * `QQQ_20200207_C_00219000` is four fields joined: the underlying, the expiry, the right,
 * and the strike in thousandths of a dollar. The readable form is what the plan asks a
 * contract to be shown as, with the original kept on hover — so the id a log or an OCC
 * symbol would carry is still one hover away. */
export function contractParts(id){
  const m = /^([A-Z.]+)_(\d{4})(\d{2})(\d{2})_([CP])_(\d+)$/.exec(String(id || ""));
  if(!m) return {underlying: "—", readable: String(id || "—"), raw: String(id || "")};
  const [, underlying, y, mo, d, right, strike] = m;
  return {
    underlying,
    expiry: `${y}-${mo}-${d}`,
    right: right === "C" ? "call" : "put",
    strike: Number(strike) / 1000,
    readable: `${underlying} ${y}-${mo}-${d} ${Number(strike) / 1000} ${right}`,
    raw: String(id),
  };
}

/** One marker on the lifecycle strip: when, which slot, what it did. */
function markers(row){
  const closing = SLOT_OF_EXIT[row.exit_reason];
  return [
    {date: row.entry_date, slot: 2, action: "OPEN",
     says: "the entry slot bought the contract"},
    {date: row.exit_date, slot: closing, action: "CLOSE",
     says: closing != null
       ? `the ${SLOT[closing]} slot closed it`
       : `closed by ${REASON[row.exit_reason] || row.exit_reason} — a lifecycle setting, `
         + `not a slot`},
  ].filter(m => m.date);
}

/** The level the closing rule was set to, read from the configuration this run carried.
 *
 * The plan asks the action log to carry "the data values that triggered it". The per-action
 * values are in the decision log, which no endpoint serves; the **level the rule was holding
 * to** is in the config, and it is the number a reader checking a stop against its setting
 * actually wants. */
function triggerLevel(row, config){
  const exit = (config && config.exit) || {};
  const at = {
    take_profit: exit.take_profit != null ? `set at ${pct(exit.take_profit)} of premium` : "",
    stop_loss: exit.stop_loss != null ? `set at ${pct(exit.stop_loss)} of premium` : "",
    time_exit: exit.hold_days != null ? `cap of ${exit.hold_days} trading days` : "",
    dte_floor: exit.dte_floor != null ? `floor of ${exit.dte_floor} days to expiry` : "",
  }[row.exit_reason];
  return at || "";
}

/** The lifecycle strip: four states, with a marker per action in its slot's colour. */
function lifecycle(row, config){
  const states = ["idle", "armed", "open", "closed"];
  return `<div class="tc-life">
    <ol class="tc-states">${states.map((s, i) =>
      `<li class="tc-state${i >= 2 ? " reached" : " assumed"}">${s}</li>`).join("")}</ol>
    <p class="hint">Idle and armed are not recorded per trade — the candidate states before
      an open are in the preview, which has no endpoint yet. Open and closed are this
      trade's own dates.</p>
    <ul class="tc-actions">${markers(row).map(m => `
      <li class="tc-action">
        <span class="tc-dot" style="${m.slot != null
          ? `background:var(--slot-${m.slot})` : "background:var(--text-muted)"}"
          aria-hidden="true">${m.slot ?? "·"}</span>
        <span class="tc-when">${esc(dateLong(m.date))}</span>
        <span class="tc-what">${esc(m.action)}</span>
        <span class="tc-why">${esc(m.says)}${
          m.action === "CLOSE" && triggerLevel(row, config)
            ? ` · ${esc(triggerLevel(row, config))}` : ""}</span>
      </li>`).join("")}</ul>
  </div>`;
}

/** The signals the engine wrote at entry, which is this trade's context.
 *
 * **Only the `entry_sig_*` columns.** The contract's own measurements — the spot, the IV, the
 * delta, the days to expiry — are in the two blocks above, and listing them again put the same
 * quantity on screen twice in one card at two precisions: `fmtVal` rounds a spot of 218.99 to
 * 219 for a table column while the block above writes 218.99. One number, read two ways, in
 * one card. */
function context(row){
  const keys = Object.keys(row)
    .filter(k => k.startsWith("entry_sig_"))
    .filter(k => !absent(row[k]));
  if(!keys.length)
    return `<p class="hint">This run's trade log kept no signal values at entry, so what the
      setup and entry slots saw when they acted is not recoverable from it.</p>`;
  return `<dl class="tc-context">${keys.map(k =>
    `<dt>${esc(label(k))}</dt><dd>${esc(fmtVal(k, row[k]))}</dd>`).join("")}</dl>`;
}

/** One leg, which is every leg this engine opens.
 *
 * The plan's table has a role column because a trade may carry a hedge leg; the event-driven
 * engine opens one position per trade, so the role is stated rather than left blank — a
 * reader comparing this against the plan should be able to see that the column is answered
 * and not missing. */
function legs(row){
  const c = contractParts(row.contract);
  const side = (row.quantity ?? 0) < 0 ? "short" : "long";
  return `<div class="scroll tc-scroll"><table class="plain tc-legs">
    <thead><tr><th class="left">contract</th><th>role</th><th>side</th><th>qty</th>
      <th>in</th><th>out</th><th>fees ($)</th></tr></thead>
    <tbody><tr>
      <td class="left mono" title="${esc(c.raw)}">${esc(c.readable)}</td>
      <td>main</td><td>${side}</td>
      <td class="n">${num(Math.abs(row.quantity ?? 0), 0)}</td>
      <td class="n">${num(row.entry_price)}</td>
      <td class="n">${num(row.exit_price)}</td>
      <td class="n">${num(row.fees)}</td>
    </tr></tbody></table></div>
    <p class="hint">One leg: this engine opens one position per trade, so there is no hedge
      leg to split the P&amp;L against. The spread actually paid is in the run's execution
      tab, which has it for the whole book rather than per fill.</p>`;
}

/**
 * The card's markup.
 *
 * @param {object} row     one trade from the run's trade log
 * @param {object} context {config, position, total} — the run's configuration, and where
 *                         this trade sits in the table's current order, for "3 of 421".
 */
export function card(row, {config, position, total} = {}){
  const c = contractParts(row.contract);
  const closing = SLOT_OF_EXIT[row.exit_reason];
  return `
    <section class="tc-conclusion ${row.net_pnl >= 0 ? "good" : "bad"}">
      <p class="tc-pnl">${esc(money(row.net_pnl))}</p>
      <p class="tc-split">${esc(size(row.gross_pnl))} before costs, less
        ${esc(size(row.fees))} in fees${position && total
          ? ` · trade ${position} of ${total} in this order` : ""}</p>
      <p class="tc-closed">closed by <b>${esc(REASON[row.exit_reason] || row.exit_reason
        || "—")}</b>${closing != null ? ` — the ${esc(SLOT[closing])} slot` : ""}</p>
    </section>

    <section class="tc-block">
      <h4>what it was</h4>
      <dl class="tc-facts">
        <dt>contract</dt><dd class="mono" title="${esc(c.raw)}">${esc(c.readable)}</dd>
        <dt>expiry</dt><dd>${esc(expiry(c.expiry, row.entry_dte_calendar))}</dd>
        <dt>opened</dt><dd>${esc(dateLong(row.entry_date))}</dd>
        <dt>closed</dt><dd>${esc(dateLong(row.exit_date))}</dd>
        <dt>held</dt><dd>${esc(num(row.holding_days, 0))} trading days</dd>
        <dt>moneyness</dt><dd>${esc(pct(row.entry_moneyness))} at entry</dd>
        <dt>premium</dt><dd>${esc(num(row.entry_price))} in → ${esc(num(row.exit_price))} out,
          per contract as quoted</dd>
      </dl>
    </section>

    <section class="tc-block">
      <h4>how it ran</h4>
      <dl class="tc-facts">
        <dt>best it reached</dt><dd class="pos">${esc(pct(row.mfe))}</dd>
        <dt>worst it reached</dt><dd class="neg">${esc(pct(row.mae))}</dd>
        <dt>spot</dt><dd>${esc(num(row.entry_underlying_price))} →
          ${esc(num(row.exit_underlying_price))}</dd>
        <dt>delta</dt><dd>${esc(num(row.entry_delta, 2))} →
          ${esc(num(row.exit_delta, 2))}</dd>
        <dt>IV</dt><dd>${esc(pct(row.entry_iv))} → ${esc(pct(row.exit_iv))}</dd>
      </dl>
      <p class="hint">Both excursions are the engine's own, in premium terms, and the date
        each happened on is not kept with them. The price and P&amp;L path over the hold
        needs the TradeBook endpoint, and the dashed shadow-tracking tail after the exit
        needs the prices the engine keeps past it — neither is served yet.</p>
    </section>

    <section class="tc-block">
      <h4>its life</h4>
      ${lifecycle(row, config)}
    </section>

    <section class="tc-block">
      <h4>legs</h4>
      ${legs(row)}
    </section>

    <section class="tc-block">
      <h4>what it was bought on</h4>
      <p class="hint">Every measurement the engine wrote at entry. This is the trade's
        context: the values the setup and entry slots saw when they acted.</p>
      ${context(row)}
    </section>`;
}

/** The controls in the card's header. Each is an action on **this** trade. */
export function actions(row){
  const closing = SLOT_OF_EXIT[row.exit_reason];
  return `
    <button type="button" class="chip" id="tradeLink"
      data-tip="Copy a link that opens this trade's card, with the tab, range and filters
                that are on now.">copy link</button>
    <button type="button" class="chip" id="tradeLocate"
      data-tip="Shade this trade's holding period across every chart and take the table to
                its row.">locate on chart</button>
    ${closing != null ? `<button type="button" class="chip" id="tradeSlot"
      data-tip="Filter the whole page to the trades the ${esc(SLOT[closing])} slot closed."
      >filter to ${esc(SLOT[closing])}</button>` : ""}`;
}
