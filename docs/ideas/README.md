# `docs/ideas/` — the backlog, and the five filters

One file: [`backlog.md`](backlog.md). Candidate directions, each scored on five filters.
Written by the `/explore` debate (`.claude/skills/explore/SKILL.md`), which runs three
roles against each other — a proposer, a practitioner, a sceptic — so that a direction
arrives already argued with rather than merely liked.

**The human decides what goes to `research-lead`.** Nothing moves from here into the
research plan or into development on an agent's judgement. An idea's score is an input to
that decision, not the decision.

## The five filters

Every surviving direction is scored on all five. A direction that cannot be scored on one
of them is not ready, and saying which one is missing is more useful than a number.

1. **Whose real problem does it solve, and what is the evidence?** A named person or role,
   and something they said or did. "Researchers would want this" is not evidence.
2. **How does it connect to what already exists?** The platform, the guards, the domain
   knowledge, the data. A direction that uses none of them is a different project.
3. **Do the data, the time and the skills suffice?** Specifically: can it be answered on
   the synthetic chain, or does it need real option quotes — which is the constraint that
   rules out most option-side work today.
4. **What is the cheapest test that could be run within a week?** If the smallest version
   takes a month, the answer to that is the first thing to find.
5. **What result would kill it?** Fixed now, before any work. A direction with no such
   result cannot be abandoned, and so will not be.

## The shape of an entry

```markdown
## <the direction, as a claim about what becomes possible>

**Added:** YYYY-MM-DD · **Status:** candidate | testing | killed (<date>, why) | promoted
  to spec

- **Whose problem, with evidence:**
- **Connects to:**
- **Data, time, skills:**
- **Cheapest week-long test:**
- **What kills it:**

**The sceptic's strongest objection, and the answer to it.** Verbatim rather than
summarised — the summary is always kinder than the objection was.
```

## Why "what kills it" is required rather than encouraged

A backlog of directions none of which can be ruled out grows without bound and is read by
nobody. The kill condition is the only thing that makes the list shrink, and it has to be
written while the idea is still exciting, which is precisely when nobody wants to.
