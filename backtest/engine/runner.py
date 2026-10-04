"""Running a backtest in pieces, and running many of them — requirement 5.1.

## What the document asks for

"A runner with state snapshots for resume, and a batch runner."

## Why resume is not only about long runs

The obvious use is a run that was interrupted. The useful one is **the question a
checkpoint makes askable**: take the book as it stood at the end of 2022 and continue it
under a different rule. Without a checkpoint that comparison needs two full runs from the
start, and the two then differ in every trade before the change as well as after it --
so the difference cannot be attributed to the change.

## The thing a resumed run must not do

Differ from a continuous one. That is the whole correctness criterion, and it is a hard
one to meet by inspection: a run's state is a ledger, a queue of unfilled orders, the open
trades, each candidate's lifecycle, every stateful component's per-trade state, and a
handful of counters. Omit any one and the resumed run produces a plausible result that is
not the same result -- a trade missing, a stop starting again from its entry, a candidate
re-arming when it should be waiting.

So this module is deliberately thin and the test is the mechanism: ``tests/test_runner.py``
runs a range continuously, runs it as two halves across a checkpoint, and requires the
trade tables to be **row-identical**. An omitted field fails it.

## The batch runner

A list of configurations, run in sequence, each with its own result and its own id. In
sequence rather than in parallel: the runs share a data cache and a chain source, and two
processes generating the same synthetic day would be doing the work twice and could
disagree if the generator ever acquired any state. Parallelism here is an optimisation
with a correctness risk attached, and the document asks for a batch runner rather than a
fast one.

It also records **how many configurations were tried**, which is the number the deflated
Sharpe needs and the number people forget. A batch of forty is forty trials whether or not
anyone writes that down, so the runner writes it down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterator, Mapping, Sequence

from backtest.core.errors import InvariantError, StartupError


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A run's state at the end of a session, enough to continue from.

    Every field is something the next session reads. The list is the specification, and
    the round-trip test is what holds it to the specification -- a field added to the
    loop and not to this produces a resumed run that differs, which the test catches and
    review would not.
    """

    #: The last session the run completed. Resuming starts at the next one.
    through: date
    #: The strategy's own name, checked on resume. A checkpoint from one strategy
    #: continued under another would be a book nobody built.
    strategy_name: str
    #: The run fingerprint of the run that produced it, for the same reason and one level
    #: stricter: two runs of the same strategy on different costs have the same name.
    run_id: str

    ledger: Any
    queue: tuple = ()
    open_trades: Mapping[int, Any] = field(default_factory=dict)
    records: tuple = ()
    snapshots: tuple = ()
    greeks: tuple = ()
    paths: tuple = ()
    shadow: tuple = ()
    actions: tuple = ()
    shadowing: Mapping[int, Any] = field(default_factory=dict)
    deliveries: Mapping[str, date] = field(default_factory=dict)
    candidates: Mapping[str, Any] = field(default_factory=dict)
    next_trade_id: int = 1
    #: slot -> that component's ``get_state()``. The per-trade state of every stateful
    #: form; without it a trailing stop resumes from its entry and is a fixed stop for
    #: the rest of the trade.
    component_state: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: The equity figures the risk rule and the plan read. Counters rather than state in
    #: the usual sense, and the easiest group to forget: a resumed run whose peak equity
    #: restarted at the initial capital reports no drawdown until it exceeds the old peak.
    counters: Mapping[str, Any] = field(default_factory=dict)
    #: The event stream so far, and the next id to hand out.
    #:
    #: **Found by the equality test, which is what it is for.** An order still in flight
    #: carries the id of the ORDER event it was published as, so the fill can name it.
    #: A resumed run with a fresh bus starts ids at 1, and the carried order then points
    #: at an event that has not happened -- the bus refuses it outright, which is the
    #: good case. The bad case would have been a resumed run whose ids happened to
    #: collide, producing a trace that reads backwards and is not refused by anything.
    events: tuple = ()
    next_event_id: int = 1

    def describe(self) -> str:
        return (f"through {self.through}: {len(self.records)} closed trade(s), "
                f"{len(self.open_trades)} open, {len(self.queue)} order(s) in flight, "
                f"{len(self.component_state)} stateful component(s)")


@dataclass(frozen=True, slots=True)
class BatchEntry:
    """One configuration in a batch, and what it produced."""

    label: str
    config: Mapping[str, Any]
    result: Any = None
    #: The exception, as text, when it did not run. Kept rather than raised: one bad
    #: configuration in a batch of forty should not discard the thirty-nine.
    failed: str = ""

    @property
    def ran(self) -> bool:
        return self.result is not None and not self.failed


@dataclass(frozen=True, slots=True)
class Batch:
    """What a batch produced, and how many configurations it tried."""

    entries: tuple[BatchEntry, ...]

    @property
    def trials(self) -> int:
        """**How many configurations were tried**, failures included.

        Failures count. A configuration that raised is still a thing that was looked at,
        and a deflated Sharpe computed from the ones that ran would be deflated by too
        few trials -- which flatters the survivors by exactly the number of attempts
        nobody wrote down.
        """
        return len(self.entries)

    def ran(self) -> tuple[BatchEntry, ...]:
        return tuple(e for e in self.entries if e.ran)

    def failed(self) -> tuple[BatchEntry, ...]:
        return tuple(e for e in self.entries if e.failed)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[BatchEntry]:
        return iter(self.entries)

    def describe(self) -> str:
        lines = [f"Batch · {self.trials} configuration(s) tried", "─" * 68]
        if self.failed():
            lines.append(f"  {len(self.failed())} did not run:")
            for entry in self.failed():
                lines.append(f"    {entry.label}: {entry.failed}")
        lines.append(f"  {len(self.ran())} produced a result.")
        lines.append("")
        lines.append(f"  **{self.trials} trials.** Every configuration looked at is a "
                     f"trial, whether")
        lines.append("  it ran or not, and the deflated Sharpe needs this number rather "
                     "than the")
        lines.append("  number of results. A batch of forty is forty trials even if "
                     "thirty-nine")
        lines.append("  are never mentioned again.")
        return "\n".join(lines)


def run_batch(configs: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
              *, runner) -> Batch:
    """Run each configuration in sequence.

    ``configs`` is a mapping of label to keyword arguments, or a sequence of them (labels
    are then the index).

    ``runner`` is **required**, and that is the dependency direction rather than a
    preference. This is Layer 5; :func:`backtest.run` is Layer 7, and the convenience of
    defaulting to it was a reverse import that ``tools/check_deps.py`` refused -- written
    here with a comment claiming the opposite, which the check caught. ``backtest.run``
    is where the default lives: see ``api.sdk.run_batch``.

    A configuration that raises is **recorded and the batch continues**. One bad entry in
    forty should not discard the thirty-nine, and the failure is in the result rather than
    in a log nobody reads: a batch that silently ran thirty-nine would report thirty-nine
    trials, which understates the search.
    """
    if isinstance(configs, Mapping):
        items = list(configs.items())
    else:
        items = [(str(i), c) for i, c in enumerate(configs)]
    if not items:
        raise StartupError(
            "a batch of no configurations has nothing to run. An empty batch is almost "
            "always a list comprehension that filtered everything out"
        )

    entries: list[BatchEntry] = []
    for label, config in items:
        try:
            entries.append(BatchEntry(label=label, config=dict(config),
                                       result=runner(**config)))
        except Exception as exc:                      # noqa: BLE001
            entries.append(BatchEntry(label=label, config=dict(config),
                                       failed=f"{type(exc).__name__}: {exc}"))
    return Batch(entries=tuple(entries))
