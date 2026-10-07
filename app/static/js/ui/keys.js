/* ui/keys.js — the keyboard, as one table.
 *
 * The design plan's appendix is a shortcut table and its acceptance criteria ask that the
 * core tasks be reachable "without a mouse". Both need the same thing: **one place that
 * knows every binding**, because a page that binds its own keys cannot answer "what are the
 * shortcuts" and two pages binding the same key cannot be told apart by reading either.
 *
 * So a view registers what it can do and gets back a way to take it off again. The registry
 * is also what the command palette lists, which is the reason a shortcut and a command are
 * the same record here rather than two: a reader who cannot remember `[` should be able to
 * find "previous run" by typing it, and that only works if nobody has to maintain a second
 * list.
 *
 * Two rules:
 *
 * **Typing wins.** A bare letter inside a text box is text. Every binding without a
 * modifier is skipped while the focus is in an input, a textarea, a select or anything
 * contenteditable — otherwise `l` in a strategy's name would toggle a log axis, which is the
 * kind of thing that makes a keyboard feel haunted.
 *
 * **A key is described, not only bound.** `does` is the sentence the palette shows and the
 * tooltip carries, so a binding cannot exist without a way to discover it.
 */
"use strict";

/** Every active binding, in registration order. A later scope shadows an earlier one for
 *  the same combination, which is what lets the drawer take `Escape` while it is open. */
const BINDINGS = [];

/** Whether the keystroke is going into something a person is typing in. */
function typing(target){
  if(!target) return false;
  if(target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/** The canonical form of one keystroke: modifiers in a fixed order, then the key.
 *
 * `Meta` and `Control` are one modifier called `mod` — the plan writes "Cmd on Mac, Ctrl on
 * Windows" and a binding that spelled one of them would be dead on the other platform. */
export function combo(event){
  const parts = [];
  if(event.metaKey || event.ctrlKey) parts.push("mod");
  if(event.altKey) parts.push("alt");
  if(event.shiftKey) parts.push("shift");
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  parts.push(key);
  return parts.join("+");
}

/** How a binding reads on screen: `mod+k` as `⌘K`, `ArrowDown` as `↓`. */
export function pretty(spec){
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || "");
  const names = {mod: mac ? "⌘" : "Ctrl", alt: mac ? "⌥" : "Alt", shift: "⇧",
                 ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→",
                 Enter: "↩", Escape: "Esc", " ": "Space"};
  return spec.split("+").map(p => names[p] || (p.length === 1 ? p.toUpperCase() : p))
    .join(mac ? "" : "+");
}

/**
 * Register a group of bindings.
 *
 * @param {string} scope  what owns them — "global", "run", "drawer". Shown in the palette
 *                        and used to take the whole group off at once.
 * @param {object[]} list {keys, does, run, where?} — `keys` may be several spellings of the
 *                        same command (`"["` and `"mod+["`), `where` is an extra guard
 *                        asked at the moment the key is pressed.
 * @returns {function} a function that removes this group.
 */
export function bind(scope, list){
  const group = list.map(b => ({...b, scope}));
  // Unshifted, so the most recently registered scope is found first and shadows the rest.
  BINDINGS.unshift(...group);
  return () => {
    for(const b of group){
      const at = BINDINGS.indexOf(b);
      if(at >= 0) BINDINGS.splice(at, 1);
    }
  };
}

/** Every binding that is currently live, for the palette and for a help surface. */
export function shortcuts(){
  return BINDINGS.map(b => ({scope: b.scope, keys: b.keys, does: b.does, run: b.run}));
}

/** Start listening. Called once, by the shell. */
export function startKeys(){
  document.addEventListener("keydown", event => {
    const spec = combo(event);
    const modified = spec.startsWith("mod") || spec.startsWith("alt");
    for(const b of BINDINGS){
      const keys = Array.isArray(b.keys) ? b.keys : [b.keys];
      if(!keys.includes(spec)) continue;
      // A bare letter belongs to whatever is being typed in. A modified one does not: ⌘↩
      // submits from inside the box the configuration was typed into, which is the point
      // of it.
      if(!modified && typing(event.target)) continue;
      if(b.where && !b.where()) continue;
      event.preventDefault();
      b.run(event);
      return;
    }
  });
}
