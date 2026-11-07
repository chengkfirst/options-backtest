---
name: explore
description: Run the three-role explorer debate on what this project should become — a proposer, a practitioner arguing as a real trader, and a sceptic trying to refute everything — then score the survivors on the five filters and update docs/ideas/backlog.md. Reads docs/feedback/ and the latest docs/radar/ digest first. Use when the question is what to build next at the level of direction, not features.
argument-hint: "[an area to explore, or nothing to start from the feedback and the radar]"
disable-model-invocation: true
user-invocable: true
allowed-tools: Read, Grep, Glob, Bash, Write, Edit, Agent(explorer-proposer, explorer-practitioner, explorer-skeptic), TodoWrite
---

# /explore

A debate about what this project should become. Named only: it writes
`docs/ideas/backlog.md` and it spawns several expensive agents.

## 1. Read the two inputs first, and say what they say

**`docs/feedback/`** — what actually happened in real use. The only input nothing in this
repository can derive, and therefore the only one that is evidence rather than reasoning.

**The latest digest in `docs/radar/`** — what changed outside recently, with its "inputs for
`/explore`" list, which exists for this.

Also `docs/ideas/backlog.md`: what was proposed before, and **what was killed and why**. A
killed direction is not re-proposed unless the thing that killed it has changed — and then
the debate has to say which and why that reopens it.

Summarise both in a few lines before starting. A debate that has not read the feedback is
three agents reasoning from the same priors.

## 2. Enabling the team, or not

The three roles are meant to run **as an agent team**, so they can answer each other
directly. Agent teams are experimental and off by default, and **this project does not
enable them in any settings file** — it changes how ordinary subagents launch, which affects
every other agent here.

To enable them for one session, the human starts the session with:

```bash
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 claude
```

Then the three roles argue in one conversation, which is the better debate: the sceptic can
answer the practitioner, and the proposer can concede something specific rather than in
general.

### The fallback, when teams are not enabled

Run them as ordinary subagents, **in sequence**, passing each one the previous outputs
verbatim:

1. **`explorer-proposer`** — three to five directions, each with what becomes possible, why
   now, the nearest thing that already exists and why it falls short, and the smallest
   version. Give it the feedback and the radar digest.
2. **`explorer-practitioner`** and **`explorer-skeptic`**, in parallel, each given the
   **full text** of the proposals. They do not see each other's answers; that is the cost of
   the fallback, and it is worth naming in the output.
3. **`explorer-proposer`** again, given both responses, to **concede what lands** and defend
   what does not. One round only. A proposal that survives by rewording was killed.
4. **The synthesis** — yours, not an agent's. Below.

The fallback's real loss is that the practitioner's "I would not use this" and the sceptic's
"it already exists" never meet, and those two together usually decide. Say in the backlog
entry which mode produced it.

## 3. Score the survivors on the five filters

Yours to do, after all three have spoken — scoring is not any role's job, because each would
score to its own position. All five, for every surviving direction; **a direction that
cannot be scored on one of them is not ready, and saying which one is missing is more useful
than a number.**

1. **Whose real problem, with evidence.** A named person or role, and something they said or
   did — `docs/feedback/` or the practitioner's answer. "Researchers would want this" is not
   evidence. Where the honest answer is "no evidence either way", write that, and make the
   cheapest test aim at the demand rather than at the implementation.
2. **How it connects to what exists.** The platform, the twenty guards, the domain
   knowledge, the data. A direction that uses none of them is a different project, which may
   be the right answer and is a bigger decision than it looks.
3. **Do the data, the time and the skills suffice?** Specifically: **can it be answered on
   the synthetic chain** — constant IV, no skew, no term structure — or does it need real
   option quotes? That single question rules out most option-side work today, and a
   direction that needs real data has a hidden month in it for acquisition and cleaning.
4. **The cheapest test within a week.** If the smallest version takes a month, that is the
   first thing to find out and it goes here as the finding.
5. **What result would kill it.** Fixed now, before any work. A direction with no kill
   condition cannot be abandoned, and so will not be — which is how a backlog grows without
   bound and stops being read.

## 4. Write it

To `docs/ideas/backlog.md`, in the entry shape `docs/ideas/README.md` gives. Per direction:
the claim, the date, the status, the five filters, and:

**The sceptic's strongest objection, verbatim, with the answer to it.** Verbatim rather than
summarised — the summary is always kinder than the objection was, and the objection is the
most useful thing in the entry.

Then, at the top: **which one you would do first, and why.** Say it plainly. A list of five
equally-scored directions is a decision not made.

## 5. Stop

**The human decides what goes to `research-lead`.** Nothing here moves into the research plan
or into development on an agent's judgement. A score is an input to that decision.

Do not write a spec, do not open a branch, do not start the cheapest test. The cheapest test
is a proposal too.
