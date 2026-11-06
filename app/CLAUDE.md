# `app/` — the interface

[conventions.md](../docs/conventions.md) is the rulebook; this is what applies here in
particular. Read the rulebook first — this file does not repeat it.

## Declare it in `CONTRACT` before you read it

**Nothing in the interface may read a response field that is not declared in `CONTRACT`**
(`static/js/contract.js`), at the depth it is read — so a renamed nested field fails too.

One declaration, used in two places: the interface checks it at runtime, so a missing field
is reported at once and `undefined` never seeps into rendering; and
`tools/check_wiring.py` checks the same declaration against a real response before a
commit. The order matters — **add the field to `CONTRACT` first**, then read it.

This has failed three times here, and every time it failed silently: the run history
showing `undefined`, every group label coming out empty, and the interface still sending a
sizing method's old name after it was renamed. Both sides were defined, so nothing raised;
`find()` matched `undefined` against `undefined`.

The backend half of the same rule: a form field must be a real `run()` parameter, or
filling it in has no effect and nothing raises. `tests/test_components.py` checks every
field against `run()`'s signature, `app/runner.py`'s assembly, and the whole
`static/js/` tree.

## Review every change in `states.html`

`static/states.html` is the component gallery: every component in every state, rendered
from the real modules — empty, loading, one row, many rows, an error, absence. Looking at
the page shows you the state you happened to produce. The gallery shows you the ones you
did not think about, which is where the interface's failures actually live.

## There is no bundler

Modules are loaded as ES modules, straight from disk. So an import of a name nothing
exports, or a name used without importing it, raises **only when that code path runs** —
which may be the first time a user hits an error branch. `tools/check_modules.py` is what
makes it fail before the commit instead; run it to see how many modules and exported
names it covers.

Consequences worth holding in mind: no transpiling, so write what the browser runs; no tree
shaking, so a module imported for one function costs the whole file; and relative paths are
real paths, so moving a file breaks every importer and `check_modules` is what says so.

## The five guards that live here

| Guard | What it catches |
|---|---|
| `check_wiring.py` | the interface reading a field the server never sends, at the depth it is read |
| `check_modules.py` | an import of a name nothing exports, or a name used but not imported |
| `check_contrast.py` | a colour pair too close to read, **in either theme**. Written against the design plan's own palette, which failed its 4.5:1 rule in twenty text pairs and had one slot colour invisible on the dark ground |
| `check_words.py` | two words for one concept on screen, a button naming a verb with no object, an exclamation mark, a "please" |
| `check_selftest.py` | the browser self-test quietly stopping covering a module. It prints the case and module counts; the suite is at `/selftest.html` |

`check_words` is the one that surprises people. The design plan has a terms table because a
reader who meets both "component" and "slot" has to work out whether they are the same
thing, and that work is the interface's failure, not theirs.

## No silent failure, on the page

Every error and every skipped thing goes through `notice()` into the problem strip at the
top. **No empty `catch`, and no quiet early return.** A blank in the browser with no reason
given is worse than a crash: a crash can be read.

Absence is rendered as absence, never as a number. A metric that could not be computed says
so; it does not print 0.

## What this directory may and may not do

- **May not** edit `backtest/`. If the interface needs a field or a parameter that does not
  exist, that is a request to the engine session — write it as a spec in `docs/specs/`.
- **Visible starting values are allowed.** The form ships one for every field: on screen,
  in the saved configuration, one edit away. A value that fills itself in when a field is
  omitted and then appears nowhere is not. `tools/check_defaults.py` holds the line.
- **The form is generated**, from `COMPONENTS` in `backtest/core/slots.py` by way of
  `/api/meta`. Adding a field by hand-coding it in the composer puts a second definition
  beside the first, and two definitions always drift.

## Running it

```bash
/opt/anaconda3/bin/python app/server.py                   # http://localhost:8765
BENCH_PORT=8766 /opt/anaconda3/bin/python app/server.py   # a second instance beside it
```

Localhost only. The composer is `/`, the gallery `/states.html`, the self-test
`/selftest.html`.
