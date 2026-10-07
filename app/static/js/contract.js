/* contract.js — what the interface needs from the backend, declared once.
 *
 * Both sides use this declaration:
 *
 * - **at runtime**: a response is checked against it and anything missing is reported at
 *   once, rather than letting `undefined` seep into the render functions. When the
 *   backend returned `id` and the interface read `run_id`, `RUNS.find()` "matched"
 *   because both sides were undefined, and it still looked like it worked.
 * - **before committing**: `tools/check_wiring.py` reads this same file and checks the
 *   declaration against a real response.
 *
 * Scanning the JS source to guess field names is unreliable — it misses forms like
 * `RUNS[0].x` — hence the declaration.
 *
 * A path may be **nested**, written with dots, and `*` descends into every element of a
 * list: `config.select.dte_lo`, `rows.*.net_pnl`, `scenarios.*.max drawdown`. Top-level
 * keys alone were not enough: `cfgLine` read `config.select.dte` for weeks after the
 * backend renamed it to `dte_lo`/`dte_hi`, the run history printed "undefinedDTE", the
 * same string was handed to Claude as the strategy's description, and the guard stayed
 * green because `config` itself was present. A field is declared at the depth it is read.
 */
"use strict";

import {notice} from "./net.js";

export const CONTRACT = {
  run: ["id", "rows", "config", "engine", "started", "seconds",
        "n_trades", "win_rate", "net_pnl", "boundary", "problems",
        "diagnostics", "anomalies", "manifest", "events", "synthetic",
        // Which validation tools this run can answer. Declared per run rather than
        // assumed, because the counterfactual needs the shadow prices only the
        // event-driven engine keeps.
        "validations",
        // What `describeConfig` reads to say what ran.
        "config.symbol", "config.start", "config.end", "config.entry", "config.engine",
        "config.select.right", "config.exit.take_profit", "config.exit.stop_loss",
        "config.exit.hold_days", "config.exit.dte_floor", "config.sizing.method",
        // The trade columns every chart and table reads. The column allowlist in
        // runner.py has dropped one of these before, and every group then came out empty.
        "rows.*.net_pnl", "rows.*.gross_pnl", "rows.*.fees", "rows.*.entry_date",
        "rows.*.exit_date", "rows.*.exit_reason", "rows.*.holding_days",
        "rows.*.contract", "rows.*.entry_price", "rows.*.exit_price",
        "rows.*.entry_iv", "rows.*.entry_delta",
        "diagnostics.*.kind", "diagnostics.*.count", "diagnostics.*.first",
        "diagnostics.*.last", "diagnostics.*.reasons",
        // The conclusion tier of the analysis page. Every one of these is a number the
        // backend computed; the page renders them and derives none of its own.
        // Whether this run has been archived. The run page reads it to decide whether its
        // control says archive or restore, so a rename would silently leave the button
        // offering the wrong one.
        "archived",
        "analysis", "analysis.intervals", "analysis.investor", "analysis.costs",
        "analysis.by_exit_reason", "analysis.excursion",
        // The headline comparison: the same first-order exposure held in shares. A list,
        // so the element fields are declared with `*`.
        // The backend's own trade statistics, which no surface read until the plan's
        // audit found them.
        "analysis.trade_stats", "analysis.trade_stats.profit_factor",
        "analysis.trade_stats.avg_win", "analysis.trade_stats.avg_loss",
        "analysis.baselines", "analysis.baselines.*.name",
        "analysis.baselines.*.strategy", "analysis.baselines.*.baseline",
        "analysis.baselines.*.excess", "analysis.baselines.*.note",
        "analysis.baselines.*.valid_on_synthetic",
        "analysis.intervals.net_pnl.estimate", "analysis.intervals.net_pnl.low",
        "analysis.intervals.net_pnl.high", "analysis.intervals.net_pnl.level",
        "analysis.intervals.net_pnl.caveat",
        // The mean per trade is read from the bootstrap's own estimate rather than divided
        // out of the total, so the figure on the card and the interval under it come from
        // the same computation.
        "analysis.intervals.mean_pnl.estimate", "analysis.intervals.mean_pnl.low",
        "analysis.intervals.mean_pnl.high", "analysis.intervals.mean_pnl.level",
        "analysis.investor.Sortino", "analysis.investor.Calmar",
        "analysis.investor.top5_share", "analysis.investor.without_top5",
        // Alpha, not the difference of two totals: a strategy with twice the exposure
        // should beat the underlying by that much, and subtracting the totals credits the
        // leverage to the strategy. Beta rides with it in the same card's note.
        "analysis.investor.annualised_alpha", "analysis.investor.beta_vs_underlying",
        "analysis.investor.buy_and_hold", "analysis.investor.strategy_total",
        "analysis.excursion.censoring",

        // The trade card. Every block of it is a stored column of the trade log; a renamed
        // column has to fail here rather than leaving a card with blanks in it.
        "rows.*.trade_id", "rows.*.quantity", "rows.*.mfe", "rows.*.mae",
        "rows.*.entry_underlying_price", "rows.*.exit_underlying_price",
        "rows.*.exit_iv", "rows.*.exit_delta", "rows.*.entry_dte_calendar",
        // What the experiment log and the comparison read off a run: which idea and version
        // it belongs to, when it ran, what it covered, and how it is filed.
        "started", "strategy_name", "strategy_ver", "symbol", "start_date", "end_date",
        "synthetic", "n_notes", "last_verdict", "fingerprint",
        // The realism strip: what determined this result, restated where it is read.
        "manifest.run_id", "manifest.code", "manifest.config.notional_capital",
        "manifest.config.costs.adverse_fraction", "manifest.execution.execution_lag_days",
        "manifest.execution.shadow_days", "manifest.provenance.all_synthetic",
        "manifest.provenance.hold_out", "manifest.guards.state"],
  /* Read defensively, and genuinely absent when nobody asked for one.
   *
   * Selection is a feasible region, not a pinned point, and "blank means no limit" — the
   * runner only passes on the fields that were filled in, so a configuration without an
   * open-interest floor has no `min_open_interest` key at all. The interface renders each
   * of these only when it is there.
   *
   * They are still declared, because a **rename** has to fail somewhere: `check_wiring`
   * checks them against a sample run that fills every one in, which it can do because it
   * writes its own payload. The runtime check cannot — it sees whatever was configured,
   * and demanding these there reported a missing field on every ordinary run, which is
   * how a notice strip gets ignored.
   */
  /* The cost breakdown, which changed shape when the spread moved into it.
   *
   * Declared read-when-present rather than required: a run stored before the change carries
   * `fees_pct_of_gross` and none of these, and demanding them would put a red strip above
   * every older run in the store. `check_wiring` still holds them, because its sample is a
   * fresh run and fills every one in — which is where a rename has to fail.
   */
  run_when_set: ["analysis.costs.total", "analysis.costs.fees",
                 "analysis.costs.premium_traded", "analysis.costs.cost_pct_of_premium",
                 "analysis.costs.fees_pct_of_premium", "analysis.costs.gross_pnl",
                 "analysis.costs.net_pnl", "analysis.costs.eaten_by_costs",
                 "config.select.dte_lo", "config.select.dte_hi",
                 "config.select.delta_lo", "config.select.delta_hi",
                 "config.select.delta", "config.select.max_spread",
                 "config.select.min_open_interest"],
  // Only the event-driven engine has these; their absence under the trade table is normal
  run_optional: ["equity", "equity_metrics", "scenarios", "attribution",
                 "equity.*.date", "equity.*.equity_mid", "equity.*.equity_liq",
                 "equity_metrics.total_return", "equity_metrics.annualised",
                 "equity_metrics.max_drawdown", "equity_metrics.sharpe",
                 "equity_metrics.annualised_vol", "equity_metrics.mean_capital_used",
                 "attribution.totals", "attribution.high_residual",
                 "scenarios.*.scenario", "scenarios.*.from", "scenarios.*.to",
                 "scenarios.*.sessions", "scenarios.*.return",
                 "scenarios.*.max drawdown", "scenarios.*.note"],
  /* One row of `/api/runs`, which is the experiment log's whole list.
   *
   * Declared separately from `run` because it is a different shape: the list carries what a
   * row needs to be read and compared, and not the trade log or the daily curve — 172 runs
   * with their curves attached is a payload nobody asked for. A field read in the list has to
   * be declared here even when `run` already declares it, because the two endpoints are
   * free to drift apart and the list is the one that would fail silently.
   */
  runs: ["id", "started", "strategy_name", "strategy_ver", "symbol",
         "start_date", "end_date", "n_trades", "net_pnl", "win_rate", "synthetic",
         "archived", "n_notes", "last_verdict", "sharpe_annualised", "fingerprint",
         "config", "config.engine", "manifest", "analysis"],
  /* The columns of the list that only some runs have.
   *
   * A trade-table run has no equity metrics and a run saved before a part of the analysis
   * existed has none of it, and both are **normal**. Demanding them of the first row of the
   * list would put a red "the backend is missing fields" strip above an ordinary list, which
   * is how a notice strip gets ignored. `check_wiring` still holds them: its sample run is
   * event-driven and fills every one in, so a rename fails there.
   */
  runs_when_set: ["manifest.execution.execution_lag_days",
                  "manifest.config.costs.adverse_fraction",
                  "analysis.equity_metrics.annualised",
                  "analysis.equity_metrics.max_drawdown",
                  "analysis.investor.Sortino",
                  "analysis.intervals.net_pnl.low", "analysis.intervals.net_pnl.high",
                  "analysis.intervals.net_pnl.level"],
  /* One group of `/api/ideas`: the size of the search under an idea.
   *
   * The header of a group in the experiment log is not decoration — it is the count the
   * deflated Sharpe is deflated by. A renamed field here would leave the header reading
   * "undefined tried", which is the one number on that page a reader must be able to trust.
   */
  ideas: ["idea", "runs", "archived", "distinct", "reruns", "unplaced", "symbols",
          "last", "best_sharpe", "hold_out", "hold_out_touched", "verdicts",
          // The best Sharpe and what the search does to it travel together, so a header
          // cannot show the maximum of sixty tries without its discount — or without the
          // reason there is none.
          "best_run", "deflated"],
  meta: ["symbols", "defaults", "form", "signal_docs", "plan_help", "code_help", "gaps",
         // Measured runtimes, so the composer's estimate is a measurement rather than a
         // number somebody typed. Absent where too few runs are on record, which the
         // interface says rather than papering over.
         "pace",
         "validations",
         // Every registered form of every slot. The composer's variant pickers and their
         // parameter forms are generated from it, so a slot losing its forms here would
         // silently remove the choice rather than report it.
         "components", "components.stop_loss", "components.take_profit",
         "components.headwind",
         // Every cut a verdict on the analysis page names. Declared so that a renamed or
         // retired threshold fails here rather than leaving a flag that silently stops
         // firing — which is the worst way for a guardrail to end.
         "thresholds", "thresholds.bootstrap_min_observations.value",
         "thresholds.cost_share_warning.value", "thresholds.single_trade_share.value"],
  sizing_methods: ["fixed", "risk_pct", "fixed_notional", "scale_by_vol"],
};

/** Resolve one declared path against a response.
 *
 * Returns "ok", "missing" or "empty" — and "empty" is **not** a failure: a list with no
 * elements (no anomaly fired, no scenario covered) cannot show whether its fields are
 * there, and reporting that as missing would cry wolf on every quiet run.
 *
 * A key that is present and holds `null` is "empty" for the same reason. `null` is the
 * store's word for **not recorded** — a run saved before a part of the analysis existed
 * keeps the column and leaves it null — and that is a fact about one old run, not a sign
 * that the two sides have drifted. Treating it as missing put a red "the backend response
 * is missing fields" banner above every stored run the moment a new analysis part shipped.
 * A rename or a removal still fails, because then the key itself is gone.
 */
export function resolvePath(obj, path){
  let node = obj;
  for(const part of path.split(".")){
    // Reaching here with a null node means some earlier key existed and held null; an
    // absent key returns "missing" below, before the walk can continue.
    if(node == null) return "empty";
    if(part === "*"){
      if(!Array.isArray(node)) return "missing";
      if(!node.length) return "empty";
      node = node[0];
      continue;
    }
    if(typeof node !== "object" || !(part in node)) return "missing";
    node = node[part];
  }
  return "ok";
}

export function checkContract(record){
  const missing = CONTRACT.run.filter(p => resolvePath(record, p) === "missing");
  if(missing.length)
    notice(`the backend response is missing fields: ${missing.join(", ")}. Something on `
      + `the interface will not render — the two sides are out of step.`);
  // The event-driven-only fields are checked only when the section they feed is present.
  // Under the trade table their absence is how the interface knows to hide the section.
  const partial = CONTRACT.run_optional.filter(p => {
    const root = p.split(".")[0];
    return record[root] != null && resolvePath(record, p) === "missing";
  });
  if(partial.length){
    const roots = [...new Set(partial.map(p => p.split(".")[0]))];
    notice(`the backend sent ${roots.join(", ")} without: ${partial.join(", ")}. That `
      + `section will render incomplete.`);
  }
}

/** One row of the list, checked once rather than per row.
 *
 * Every row of `/api/runs` comes out of the same SELECT, so a field missing from the first is
 * missing from all of them; checking each would report the same fault 172 times. */
export function checkRuns(rows){
  if(!rows || !rows.length) return;
  const missing = CONTRACT.runs.filter(p => resolvePath(rows[0], p) === "missing");
  if(missing.length)
    notice(`/api/runs is missing: ${missing.join(", ")}. The experiment log reads those, `
      + `so part of every row will be blank.`);
}

export function checkIdeas(groups){
  if(!groups || !groups.length) return;
  const missing = CONTRACT.ideas.filter(p => resolvePath(groups[0], p) === "missing");
  if(missing.length)
    notice(`/api/ideas is missing: ${missing.join(", ")}. An idea's header reads those, and `
      + `the trial count is the number the deflated Sharpe depends on.`);
}

export function checkMeta(meta){
  const missing = CONTRACT.meta.filter(p => resolvePath(meta, p) === "missing");
  if(missing.length)
    notice(`/api/meta is missing: ${missing.join(", ")}. The form is generated from it, `
      + `so part of it will not appear.`);
}
