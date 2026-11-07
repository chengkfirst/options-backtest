/* configdiff.js — what differs between two configurations, computed once.
 *
 * Two surfaces need it and the design plan describes it twice: the experiment log's "what
 * differs from the idea's baseline, up to 3 items", and the comparison view's "full config
 * tree with differing values highlighted and identical branches collapsed". One
 * implementation, because a diff that disagreed with itself between the list and the
 * comparison would be worse than either.
 *
 * **Flattened to paths.** A configuration is a shallow tree — `select.dte_lo`,
 * `exit.take_profit`, `components.stop_loss.params.multiple` — and the unit a researcher
 * thinks in is the leaf, not the branch: "it differs in the stop loss" means one leaf moved.
 * Comparing whole objects would report `exit` as different and leave the reader to find out
 * which of its six fields it was.
 *
 * Code fields are compared but never printed in full: `entry_code` differing is the fact,
 * and two hundred lines of Python in a table cell is not a fact anybody reads.
 */
"use strict";

/** Paths whose value is source code: reported as changed, shown as a length. */
const CODE = new Set(["entry_code", "plan_code", "signals_code", "review_code", "signals"]);

/** One configuration as {path: value}, leaves only.
 *
 * `null` and `""` are kept rather than skipped: "this field was cleared" and "this field was
 * never there" are different changes, and a flatten that dropped both would report them as
 * the same.
 */
export function flatten(config, prefix = "", out = {}){
  if(config == null || typeof config !== "object"){
    out[prefix] = config;
    return out;
  }
  if(Array.isArray(config)){
    // An array is a leaf here. Nothing in a configuration is a list whose elements a reader
    // compares one by one, and indexing into one would make inserting an element read as
    // every later element having changed.
    out[prefix] = config.length ? config.join(", ") : "";
    return out;
  }
  for(const [key, value] of Object.entries(config)){
    const path = prefix ? `${prefix}.${key}` : key;
    if(value != null && typeof value === "object" && !Array.isArray(value))
      flatten(value, path, out);
    else out[path] = Array.isArray(value) ? value.join(", ") : value;
  }
  return out;
}

/** How a value reads in a diff cell. */
export function shown(path, value){
  const leaf = path.split(".").pop();
  if(value == null || value === "") return "—";
  if(CODE.has(leaf) || CODE.has(path)){
    const lines = String(value).split("\n").length;
    return `${lines} line${lines === 1 ? "" : "s"}`;
  }
  if(typeof value === "number") return String(Number(value.toFixed(6)));
  const text = String(value);
  return text.length > 40 ? text.slice(0, 40) + "…" : text;
}

/**
 * Every leaf that differs, in a stable order.
 *
 * @returns {{path: string, values: any[]}[]} one entry per differing leaf, `values` in the
 *          order the configurations were given. A path absent from one of them reads as
 *          `undefined`, which `shown` prints as "—" — the honest rendering of "this
 *          configuration does not have that field at all".
 */
export function diff(configs){
  const flat = configs.map(c => flatten(c || {}));
  const paths = [...new Set(flat.flatMap(f => Object.keys(f)))].sort();
  const out = [];
  for(const path of paths){
    const values = flat.map(f => f[path]);
    const same = values.every(v => String(v ?? "") === String(values[0] ?? ""));
    if(!same) out.push({path, values});
  }
  return out;
}

/** The diff as one line, for a table cell: the first few leaves, named. */
export function summary(a, b, limit = 3){
  const items = diff([a, b]);
  if(!items.length) return "";
  const head = items.slice(0, limit)
    .map(({path, values}) => `${path.split(".").pop()} ${shown(path, values[1])}`)
    .join(" · ");
  return items.length > limit ? `${head} · +${items.length - limit} more` : head;
}
