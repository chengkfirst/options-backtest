/* net.js — every call to the service, and the problem strip.
 *
 * **The only file that names an /api path.** A fetch scattered through a view is a fetch
 * nobody can enumerate, and the front/back contract depends on being able to.
 *
 * One rule runs through it: **no silent failure**. Every error and every skipped thing
 * goes through `notice()` into the strip at the top of the page. Seeing a blank in the
 * browser without knowing why is worse than a crash, so there is no empty catch here and
 * no quiet early return.
 */
"use strict";

import {$, mark, show} from "./dom.js";

/* ── the problem strip ── */
const ISSUES = [];

export function notice(msg){
  const text = String(msg);
  if(!ISSUES.includes(text)) ISSUES.push(text);
  paintIssues();
}

export function clearIssues(){
  ISSUES.length = 0;
  paintIssues();
}

export function paintIssues(){
  const box = $("issues");
  if(!box) return;
  show(box, ISSUES.length > 0);
  box.innerHTML = ISSUES.length
    ? `<b>${ISSUES.length} notice${ISSUES.length > 1 ? "s" : ""}</b><ul>${
        ISSUES.map(m => `<li>${mark(m)}</li>`).join("")}</ul>`
    : "";
}

/** Anything thrown or rejected anywhere on the page lands in the strip too. A page error
 *  that only reaches the console is a silent failure for whoever is not looking at it. */
export function catchPageErrors(){
  window.addEventListener("error", e =>
    notice(`page error: ${e.message} (${(e.filename || "").split("/").pop()}:${e.lineno})`));
  window.addEventListener("unhandledrejection", e =>
    notice(`async error: ${e.reason?.message || e.reason}`));
}

/* ── the service ── */

/** The endpoints this interface uses. Named here so they can be counted and so a typo is
 *  a missing key rather than a 404 at runtime. */
export const API = {
  health: "/api/health",
  meta: "/api/meta",
  run: "/api/run",
  runs: "/api/runs",
  ideas: "/api/ideas",
  validate: "/api/validate",
  tradeoff: "/api/tradeoff",
  note: "/api/note",
  notes: "/api/notes",
  strategies: "/api/strategies",
  runArchive: "/api/run/archive",
  runDeletion: "/api/run/deletion",
  runDelete: "/api/run/delete",
};

/** A GET returning JSON.
 *
 * Throws with a message a person can act on. The two failures that actually happen are a
 * stopped service (`fetch` rejects) and a response that is not JSON — the latter used to
 * surface as the words "not JSON" with nowhere to start, so the status and the first of
 * the body come along.
 */
export async function getJSON(path, params){
  const url = params ? `${path}?${new URLSearchParams(params)}` : path;
  let response;
  try{
    response = await fetch(url);
  }catch(e){
    throw new Error(`${url} could not be reached (${e.message}). The service may have `
      + `stopped — check the terminal it is running in.`);
  }
  return readJSON(response, url);
}

/** A POST of JSON returning JSON. */
export async function postJSON(path, body){
  let response;
  try{
    response = await fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
  }catch(e){
    throw new Error(`${path} could not be reached (${e.message}). The service may have `
      + `stopped — check the terminal it is running in.`);
  }
  return readJSON(response, path);
}

/** A refusal the service wrote, as opposed to a failure.
 *
 * "the counterfactual needs its levels" is an instruction, not a fault, and it belongs
 * where the report would have gone rather than in the problem strip. Callers tell the two
 * apart by this type. */
export class Refusal extends Error {
  constructor(message, detail){
    super(message);
    this.name = "Refusal";
    this.detail = detail || "";
  }
}

async function readJSON(response, url){
  const text = await response.text();
  let data;
  try{
    data = JSON.parse(text);
  }catch{
    throw new Error(`${url} answered with something that is not JSON (HTTP `
      + `${response.status}): ${text.slice(0, 200)}`);
  }
  // The server reports a refusal as {error, detail} with a 4xx. Both halves travel: the
  // whole error chain going back to the interface is deliberate in this project.
  if(!response.ok || data.error)
    throw new Refusal(data.error || `HTTP ${response.status}`, data.detail);
  return data;
}
