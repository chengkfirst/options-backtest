"""Does the suite notice when a mechanism is removed.

## The failure this exists for

A test whose **name** says one thing and whose **assertion** does another passes, reads
correctly, and protects nothing. It has appeared in this project in every round of review,
written by someone who knew the rule, and the reason is structural rather than careless:
the name and the docstring are written first, describing the intent; the assertion is
written afterwards, against what the code actually does; and when those two drift apart
nobody re-reads the name.

Being more careful does not fix that. Deleting the mechanism and watching the suite does.

## How it works

Each entry names a mechanism, the exact source line that implements it, a replacement that
disables it, and the tests that should fail. The replacement is applied, the tests are run,
the file is restored. A mechanism whose tests still pass **has no test**, however many
tests appear to be about it.

## What it is not

It is not mutation testing in the usual sense -- no attempt to enumerate mutants or compute
a score. The list is written by hand, one entry per mechanism someone thought worth
building. That keeps it fast enough to run routinely, and the hand-written entry is itself
the useful artefact: naming the line that implements a mechanism is a question people get
wrong, and getting it wrong here is loud.

Entries are added when a mechanism is built, not afterwards. An entry added later tends to
be written to match the test that exists rather than the behaviour that matters.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Mutation:
    """One mechanism, disabled."""

    what: str
    path: str
    #: The exact text that implements it. Must appear once; an entry whose anchor has
    #: drifted is reported rather than skipped, because a silently skipped mutation is a
    #: mechanism nobody is checking.
    anchor: str
    replacement: str
    #: Which tests must fail. Narrow, so a pass here means these tests caught it rather
    #: than something unrelated did.
    tests: tuple[str, ...]


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "slippage charged once, not twice",
        "backtest/execution/costs.py",
        "    slippage = quantity * costs.slippage_per_contract * multiplier",
        "    slippage = 0.0",
        ("tests/test_selection_costs.py::TestSlippageIsChargedOnce",),
    ),
    Mutation(
        "time exits are their own slot, not the stop's",
        "backtest/engine/loop.py",
        '    ExitReason.TIME_EXIT: "time_exit",',
        '    ExitReason.TIME_EXIT: "stop_loss",',
        ("tests/test_event_driven.py::TestDecisionLog",),
    ),
    Mutation(
        "a trade the range cannot resolve is not reported",
        "backtest/engine/vectorized.py",
        "            if record.exit_date > self.config.end:",
        "            if False and record.exit_date > self.config.end:",
        ("tests/test_engine.py::TestNoTradeOutlivesTheRange",),
    ),
    Mutation(
        "the hold-out refuses a run that reaches into it",
        "backtest/research/holdout.py",
        "    if not unlock:\n        raise StartupError(",
        "    if False:\n        raise StartupError(",
        ("tests/test_holdout.py::TestTheRefusal",),
    ),
    Mutation(
        "a position left open at the range end is reported",
        "backtest/engine/loop.py",
        "        if len(ledger):\n            # Every one of them",
        "        if False:\n            # Every one of them",
        ("tests/test_engine.py::TestAPositionOpenAtTheEnd",),
    ),
    Mutation(
        "every open position is named, not the first five",
        "backtest/engine/loop.py",
        '"; ".join(f"{p.contract} x{p.abs_quantity}" for p in ledger.positions))',
        '"; ".join(f"{p.contract} x{p.abs_quantity}" '
        "for p in list(ledger.positions)[:1]))",
        ("tests/test_engine.py::TestAPositionOpenAtTheEnd",),
    ),
    Mutation(
        "a days-to-expiry count past the calendar raises",
        "backtest/core/clock.py",
        "            if day < self.first or day > self.last:",
        "            if False:",
        ("tests/test_engine.py::"
         "TestNoTradeOutlivesTheRange::"
         "test_a_date_beyond_the_calendar_raises_rather_than_clamping",),
    ),
    Mutation(
        "the calendar runs past the bars",
        "backtest/data/market.py",
        "CALENDAR_MARGIN_DAYS = 830",
        "CALENDAR_MARGIN_DAYS = 0",
        ("tests/test_engine.py::"
         "TestNoTradeOutlivesTheRange::"
         "test_an_expiry_past_the_range_is_counted_over_the_real_calendar",),
    ),
    Mutation(
        "shares delivered at expiry are sold, not held",
        "backtest/engine/loop.py",
        "            proceeds = ledger.liquidate_stock(symbol, spot.unwrap(), day, "
        "ZERO_FEES)",
        "            proceeds = 0.0  # ledger.liquidate_stock(...)",
        ("tests/test_ledger.py::TestCashSettlementIsNamedNotHidden",),
    ),
    Mutation(
        "the day loop calls the disposal at all",
        "backtest/engine/loop.py",
        "            self._dispose_deliveries(ledger, deliveries, day)",
        "            pass  # self._dispose_deliveries(ledger, deliveries, day)",
        ("tests/test_architecture.py::"
         "test_the_day_loop_disposes_of_delivered_shares",),
    ),
    Mutation(
        # Publishing the event before the fill is exactly how the two records forked the
        # first time: `_execute` has four paths that fill nothing, and a carried intent
        # republished one every day it was retried.
        "a FILL event is published where the fill is, not where it is attempted",
        "backtest/engine/loop.py",
        "            EventType.FILL, day, \"broker\", caused_by=intent.order_event,",
        "            EventType.FILL, day, \"broker\", caused_by=0,",
        ("tests/test_chain.py::TestEveryFillIsExplainable",),
    ),
    Mutation(
        "the two records of a fill are held one to one",
        "backtest/engine/loop.py",
        "        actions.append(TradeAction(",
        "        [].append(TradeAction(",
        ("tests/test_chain.py::TestTheTwoRecordsOfAFillAgree",),
    ),
    Mutation(
        "a call early-exercise test subtracts the carry it gives up",
        "backtest/data/options/pricing.py",
        "        return dividend_in_window > 0 and time_value < dividend_in_window - carry",
        "        return dividend_in_window > 0 and time_value < dividend_in_window",
        ("tests/test_pricing.py::TestEarlyExercise",
         "tests/test_pricing.py::TestTheChainReachesBothCases"),
    ),
    Mutation(
        "the carry is the interest on the strike, not on the intrinsic",
        "backtest/data/options/pricing.py",
        "    return strike * (1.0 - math.exp(-rate * years))",
        "    return 0.0 * (1.0 - math.exp(-rate * years))",
        ("tests/test_pricing.py::TestEarlyExercise",),
    ),
    Mutation(
        "the put branch of the early-exercise flag is reachable",
        "backtest/data/options/synthetic/generator.py",
        "        if right is Right.CALL and cash <= 0:",
        "        if cash <= 0:",
        ("tests/test_pricing.py::TestTheChainReachesBothCases",),
    ),
    Mutation(
        "a passed event log is used, not silently replaced",
        "backtest/engine/loop.py",
        "        self.events = (events if events is not None\n"
        "                       else market.events)",
        "        self.events = events or market.events",
        ("tests/test_ledger.py::TestCashSettlementIsNamedNotHidden",
         "tests/test_architecture.py::"
         "test_no_optional_dependency_is_defaulted_with_or"),
    ),
    Mutation(
        "an empty choice is refused, not filled with the defaults",
        "backtest/signals/registry.py",
        "    if names is not None and not names:",
        "    if False:",
        ("tests/test_foundations.py::TestAnEmptyChoiceIsNotTheDefaultChoice",),
    ),
    Mutation(
        "a lambda sweep must include the honest fraction",
        "backtest/research/realism.py",
        "    if HONEST not in fractions:",
        "    if False:",
        ("tests/test_realism.py::TestWhereItRefusesToSpeak",),
    ),
    Mutation(
        "risk metrics come from the daily book, not the trade table",
        "backtest/research/investor.py",
        "    drawdown = curve / curve.cummax() - 1.0\n    worst = float(drawdown.min())",
        "    drawdown = curve * 0.0\n    worst = float(drawdown.min())",
        ("tests/test_investor.py::TestTheMetricsComeFromTheDailyBook",),
    ),
    Mutation(
        "the list of what counts as a ledger write is the real one",
        "tests/test_architecture.py",
        'LEDGER_WRITES = ("apply_fill", "settle_expiry", "liquidate_stock")',
        'LEDGER_WRITES = ("apply_fill",)',
        ("tests/test_architecture.py::"
         "test_the_ledger_write_list_is_the_real_one",),
    ),
    Mutation(
        "the deciding-module list points at real directories",
        "tests/test_architecture.py",
        'DECIDING_MODULES = ("backtest/signals", "backtest/portfolio")',
        'DECIDING_MODULES = ("backtest/nothing",)',
        ("tests/test_architecture.py::"
         "test_the_deciding_modules_are_the_real_ones",),
    ),
    Mutation(
        # The positive control inside the check, exercised the only way it can be: by
        # blinding the detector it guards. Disabling the control itself would be asking
        # it to catch its own removal, which nothing can do.
        "the component scan can see a ledger write when there is one",
        "tests/test_architecture.py",
        "                if isinstance(node.func, ast.Attribute) \\\n"
        "                        and node.func.attr in LEDGER_WRITES:",
        "                if isinstance(node.func, ast.Attribute) \\\n"
        "                        and node.func.attr in ():",
        ("tests/test_architecture.py::"
         "test_no_deciding_component_writes_to_the_ledger",),
    ),
    Mutation(
        "a --fast guard run does not count as green",
        "tools/check.py",
        '"passed": bool(passed and not partial),',
        '"passed": bool(passed),',
        ("tests/test_holdout.py::TestTheGuardRecord",),
    ),
    Mutation(
        "an exit component is handed a view cut at the day",
        "backtest/engine/loop.py",
        "                view=DataView.at(day, market=self.market, signals=self.signals,",
        "                view=None if True else DataView.at(day, market=self.market, "
        "signals=self.signals,",
        ("tests/test_variants.py::TestASlotReadsThroughTheView",),
    ),
    Mutation(
        "the delay test refuses to invent which lags to try",
        "backtest/research/delay.py",
        "    if not extra:",
        "    if False:",
        ("tests/test_delay.py::TestWhereItRefusesToSpeak",),
    ),
    Mutation(
        "a losing base run is not read as having decayed",
        "backtest/research/delay.py",
        "        if self.base.net_pnl < 0:",
        "        if False:",
        ("tests/test_delay.py::TestWhereItRefusesToSpeak",),
    ),
    Mutation(
        "a price offered below intrinsic value is flagged",
        "backtest/data/options/adapters.py",
        '        below = frame["ask"].astype(float) < intrinsic - 0.005',
        '        below = frame["ask"].astype(float) < -1e9',
        ("tests/test_adapters.py::TestBadDataIsFound",),
    ),
    Mutation(
        "a locked market is flagged",
        "backtest/data/options/adapters.py",
        "    if locked.any():",
        "    if False:",
        ("tests/test_adapters.py::TestBadDataIsFound",),
    ),
    Mutation(
        "a slot writes only into its own namespace",
        "backtest/core/trade_context.py",
        "        return SlotWriter(slot=slot, _into=self._slots.setdefault(slot, {}))",
        "        return SlotWriter(slot=slot, _into=self._slots.setdefault(\"shared\", {}))",
        ("tests/test_trade_context.py::TestTwoSlotsCanHoldTheSameKey",),
    ),
    Mutation(
        "the exit reason agrees with the slot that says it fired",
        "backtest/engine/loop.py",
        '        time_note.write("fired", ", ".join(fired) if fired else False)',
        '        time_note.write("fired", False)',
        ("tests/test_chain.py::TestEachSlotsRecordIsItsOwn",),
    ),
    Mutation(
        "entry is asked nothing while the candidate is idle",
        "backtest/engine/loop.py",
        "            refusal = candidate.may_enter(day, strategy.lifecycle, calendar=cal)",
        '            refusal = ""',
        ("tests/test_lifecycle.py::TestItChangesARealRun",),
    ),
    Mutation(
        "a reentry policy that waits actually waits",
        "backtest/signals/lifecycle.py",
        "        if policy == \"once\" and self.opened >= 1:",
        "        if False:",
        ("tests/test_lifecycle.py::TestEachPolicyRefusesSomething",
         "tests/test_lifecycle.py::TestItChangesARealRun"),
    ),
    Mutation(
        "the lifecycle reaches the run fingerprint",
        "backtest/api/sdk.py",
        '            "lifecycle": dataclasses.asdict(self.raw.strategy.lifecycle),',
        '            "lifecycle": None,',
        ("tests/test_lifecycle.py::"
         "TestItChangesARealRun::test_two_lifecycle_settings_do_not_share_a_run_id",),
    ),
    Mutation(
        "the config file carries the lifecycle as a value",
        "backtest/api/config_file.py",
        "    if lifecycle:\n        out[\"lifecycle\"] = lifecycle",
        "    if False:\n        out[\"lifecycle\"] = lifecycle",
        ("tests/test_lifecycle.py::"
         "TestItChangesARealRun::test_the_config_file_reproduces_a_run_with_a_policy",),
    ),
    Mutation(
        "the platform refuses to default a strategy choice",
        "backtest/signals/strategy.py",
        "    stop_loss: float | None = None",
        "    stop_loss: float | None = 0.5",
        ("tests/test_variants.py::TestParameters",
         "tools/check_defaults.py"),
    ),
)


def run_one(mutation: Mutation) -> tuple[bool, str]:
    """Disable it, run its tests, restore. Returns (caught, detail)."""
    path = ROOT / mutation.path
    original = path.read_text(encoding="utf-8")
    if mutation.anchor not in original:
        return False, "the anchor is not in the file; this mechanism is unchecked"
    if original.count(mutation.anchor) > 1:
        return False, f"the anchor appears {original.count(mutation.anchor)} times"

    path.write_text(original.replace(mutation.anchor, mutation.replacement, 1),
                    encoding="utf-8")
    try:
        failed = []
        for target in mutation.tests:
            if target.endswith(".py"):
                cmd = [sys.executable, target]
            else:
                cmd = [sys.executable, "-m", "pytest", target, "-q", "--no-header", "-x"]
            if subprocess.run(cmd, cwd=ROOT, capture_output=True).returncode != 0:
                failed.append(target)
        if failed:
            return True, f"caught by {', '.join(failed)}"
        return False, f"{', '.join(mutation.tests)} all passed with it removed"
    finally:
        path.write_text(original, encoding="utf-8")


def check() -> int:
    missed = []
    for mutation in MUTATIONS:
        caught, detail = run_one(mutation)
        print(f"  {'OK  ' if caught else 'MISS'} {mutation.what:48} {detail[:44]}")
        if not caught:
            missed.append((mutation, detail))

    print()
    if missed:
        print(f"{len(missed)} mechanism(s) can be removed with the suite still green:")
        for mutation, detail in missed:
            print(f"  - {mutation.what}\n      {detail}")
        print()
        print("A mechanism whose tests pass without it has no test, however many tests "
              "look like they are about it.")
        return 1
    print(f"OK  {len(MUTATIONS)} mechanisms, each caught when removed")
    return 0


if __name__ == "__main__":
    sys.exit(check())
