# `docs/radar/` — the weekly scout digest

Written by a scheduled cloud task, from the standalone prompt in
`.claude/scheduled/scout.md`. That task starts with no memory of this project, which is
why the prompt carries everything it needs rather than referring to anything here.

**Files:**

- `<YYYY>-W<week>.md` — one digest a week. `2026-W45.md`.
- [`seen.md`](seen.md) — every item ever reported, appended to by each digest. The
  anti-duplication record.
- [`ignore.md`](ignore.md) — things deliberately not wanted again, with the reason. Written
  by the human.

## The rules the digest is held to

**At most five items.** Not a target — a cap. A digest of twenty items is a digest nobody
reads, and once it is unread the whole mechanism is noise with a cost. Five forces the
scout to rank.

**Every item says what it means for this project, and an item with no answer to that is
dropped rather than included with the question left open.** This is the rule that makes the
difference between a digest and an RSS feed. "Interesting new backtesting library" is
dropped; "its chain handling stores one row per contract-day, which is the shape our
`chain_registry` had to build by hand — worth comparing" is kept.

**Anything in `seen.md` or `ignore.md` is skipped.** Without this, the same five libraries
arrive every week and the digest becomes something to delete on sight.

**Categories:** tools, projects, papers, data, industry.

## Where items go from here

Two lists at the bottom of each digest, which is how the radar connects to work rather than
accumulating:

- **candidates for `/benchmark`** — projects worth a proper comparison. The human supplies
  the repository list and runs `/benchmark`.
- **inputs for `/explore`** — directions worth arguing about. `/explore` reads the latest
  digest before the debate.

Neither list is acted on automatically. A scout that could start work would be a scout
whose ranking decides what this project does, and its ranking is a weekly skim.
