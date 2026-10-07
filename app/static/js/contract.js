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
        "analysis.investor.Sortino", "analysis.investor.Calmar",
        "analysis.investor.top5_share", "analysis.investor.without_top5",
        "analysis.excursion.censoring",
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
  run_when_set: ["config.select.dte_lo", "config.select.dte_hi",
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
  meta: ["symbols", "defaults", "form", "signal_docs", "plan_help", "code_help", "gaps",
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

export function checkMeta(meta){
  const missing = CONTRACT.meta.filter(p => resolvePath(meta, p) === "missing");
  if(missing.length)
    notice(`/api/meta is missing: ${missing.join(", ")}. The form is generated from it, `
      + `so part of it will not appear.`);
}
