"""The second baseline configuration: event-driven, with a stateful component.

## Why there is a second one

`tools/baseline.py` builds its trades with `TradeTableEngine`, and that engine
**refuses a strategy with components outright** (`backtest/engine/vectorized.py:108-116`,
`UNSUPPORTED["components"]`). So the configuration recorded in
`tests/baselines/baseline_trades.csv` has never had a component, and **no change to any
component can move it.** Both engine defects fixed on `fix/engine-state` were component
changes: the file was compared row for row in three states and came back identical in all
three — 230 trades, net -11,822.29 — and that agreement carried no information. See
`docs/findings/baseline-change-2026-11-06.md`.

This module is the configuration that can see that class of change. It runs the same
strategy on the same offline fixture, with slot 4 given as `stop_loss/trailing` — a
**stateful** form, whose high-water mark is a fact about the trade's history — on the
engine that can step day by day.

## Why the configuration lives here and the comparator lives in `tools/`

A baseline configuration is a **fixture**: a fixed strategy on fixed inputs, whose only
purpose is to be re-run. The first configuration already takes its inputs from
`tests/conftest.py` — bars, dividends, the seed — so the inputs were here already and only
the strategy was not. `tools/baseline.py` stays what it is: the thing that compares and
the thing that records, with the two configurations in a registry a reader can list.

The first configuration's builder is deliberately left where it is. It is the definition of
a recorded file that must not move, and moving its code between files would mean a reviewer
has to prove, line by line, that the move changed nothing.

## One file per configuration

`baseline_trades.csv` and `baseline_event_driven_trades.csv`, rather than one file with a
configuration column. Three reasons, in order of weight:

1. **The existing file does not have to be rewritten at all.** Adding a column would
   rewrite all 230 of its rows, and the one thing this change must not do is move the
   recorded results it exists to protect.
2. **A failure names the configuration in its own path.** `baseline_event_driven_trades.csv`
   in a diff header says which configuration moved before anyone reads a column.
3. **`trade_id` restarts at 1 in each configuration.** In one file the key would be
   (configuration, trade_id), which every reader of the file would have to know.

## Recording is the tool's job, not this module's

There is no writer here. The only way to write either file is
`python -m tools.baseline update --configuration <key>`, which names the configuration and
prints what it changed. A `record()` function in a module the tests import would put "just
update the baseline while I am here" one import away, and that is the move the whole module
exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache

import pandas as pd

from backtest.core.component import Bound, get
from backtest.core.config import BacktestConfig
from backtest.core.types import ContractSpec, Right
from backtest.data.market import MarketData
from backtest.data.options.synthetic.generator import SyntheticChainSource
from backtest.data.underlying import build_bars
from backtest.engine.loop import EventDrivenEngine
from backtest.engine.strategy import Strategy
from backtest.signals.contracts import SelectionRule
from backtest.signals.entry.expression import parse_condition
from backtest.signals.registry import SignalSet
from backtest.signals.strategy import EntryRule, ExitRule

from tests.conftest import DIVIDENDS, END, make_raw_bars
from tools.baseline import BASELINE_DIR, COMPARED

#: The key this configuration is named by on the command line and in `check`'s output.
#: One spelling, read by the tool's registry and by the messages here, so a rename cannot
#: leave the two disagreeing about which file is which.
KEY = "event_driven"

#: One line for `check` and `update` to print, so a reader of a failure knows what this
#: configuration is without opening this file.
DESCRIBES = ("the event-driven engine with a stateful component: slot 4 given as "
             "stop_loss/trailing rather than as a fixed level on the exit rule. The only "
             "configuration a component change can move")

EVENT_DRIVEN_FILE = BASELINE_DIR / "baseline_event_driven_trades.csv"

#: How much of the best premium the trade may give back before the stop fires.
#:
#: **A strategy parameter, so it is a choice and not a measurement.** It was chosen on
#: three grounds, measured on this fixture rather than argued:
#:
#: - **the component has to fire often.** At 0.6, 165 of 228 trades (72%) close by the
#:   stop. A trailing stop that rarely fires would give a file as blind as the one this
#:   was added to fix: stable, and stable for a reason unconnected to the component.
#: - **the file has to move when the component's rule changes.** Re-running this exact
#:   configuration with the mark started where `stop_loss/trailing` **version 1** started
#:   it -- at the fill day's exit-side ratio, 0.0198 below the entry -- moves **12 of the
#:   228 trades**, which is 23 differing rows, because the log is ordered by exit date and
#:   one trade exiting earlier shifts every row after it. It is the defect from
#:   `fix/engine-state` replayed. Twelve is the most of any fraction measured at an
#:   unchanged trade count (0.2 -> 7, 0.3 -> 8, 0.4 -> 10, 0.5 -> 4, 0.6 -> 12) -- a
#:   search over five fractions on one fixture, disclosed as one in
#:   `docs/findings/baseline-second-configuration-2026-11-08.md` and asserted nowhere.
#:   What `tests/test_baseline_event_driven.py` holds is the part that stays true whatever
#:   the fixture's numbers are: that **some** trade moves under version 1's mark, and that
#:   **none exits later** under the recorded one. Not the count -- a baseline test
#:   asserting that exactly twelve trades move would go red whenever the bars or the
#:   selection changed, which is not the thing it is guarding.
#: - **the other exits have to stay reachable.** At 0.6 the closes are 165 stop_loss,
#:   51 take_profit and 12 time_exit; at 0.5 the holding cap fires once and at 0.4 and
#:   below not at all. A fraction that swallows every other exit would make this file a
#:   test of one slot instead of a configuration that happens to exercise it.
#:
#: What it does **not** reach: `dte_floor=5` keeps every contract away from expiry, so
#: `expiry_otm` and `early_exercise` do not appear in this file. Those two paths are
#: covered by `tests/test_component_state.py`, deliberately and on its own configuration.
TRAILING_FRACTION = 0.6


@dataclass(frozen=True, slots=True)
class Inputs:
    """One run's inputs, built fresh. Nothing here is cached and nothing is shared between
    two runs: a `MarketData` carries an event log and a chain source carries a cache, so
    two runs over one set of inputs would make the baseline depend on call order."""

    config: BacktestConfig
    market: MarketData
    chain: SyntheticChainSource
    signals: SignalSet


def event_driven_inputs() -> Inputs:
    """The same offline fixture the first configuration uses, built the same way.

    Deliberately pinned to `tests/conftest.py` rather than copied from it -- the first
    configuration's dividends were once written out a second time beside the fixture's
    own list, and a change to either would have moved a recorded result for a reason that
    had nothing to do with the code.
    """
    config = BacktestConfig(
        symbol="QQQ", start=date(2022, 6, 1), end=END,
        contract=ContractSpec(symbol="QQQ", vol_proxy="^VXN"),
    )
    bars = build_bars("QQQ", make_raw_bars())
    idx = bars.frame.index
    market = MarketData(
        config, bars=bars,
        rates=pd.Series(0.045, index=idx), vol_proxy=pd.Series(0.22, index=idx),
        dividends=pd.Series(list(DIVIDENDS.values()),
                            index=pd.to_datetime(list(DIVIDENDS))),
    )
    return Inputs(config=config, market=market,
                  chain=SyntheticChainSource(market, config),
                  signals=SignalSet.from_bars(bars))


@lru_cache(maxsize=1)
def _signal_names() -> tuple[str, ...]:
    """Which signals the fixture's bars offer, which is all `parse_condition` reads.

    Read off the same construction a run uses, rather than from a second set of bars
    built here. The two could only ever agree -- and "could only ever agree" is how a
    duplicate outlives the reason it was identical.

    Cached, and the only thing here that is: a name set is a fact about the signal
    registry rather than about a run, so sharing it cannot carry state from one run into
    the next -- which is the reason :func:`event_driven_inputs` is *not* cached. The
    `Inputs` built here is discarded and the names, which are a tuple, are all that is
    kept, so nothing mutable crosses between two runs.
    """
    return event_driven_inputs().signals.names


def event_driven_strategy(stop: Bound) -> Strategy:
    """The recorded strategy, with slot 4 taken by `stop`.

    Entry, selection and the time exits are the first configuration's, unchanged, so the
    two files differ in **the form of one slot and the engine that can run it** rather
    than in the strategy. `exit.stop_loss` is absent rather than zero: the component
    decides that slot, and `Strategy.__post_init__` refuses both being given at once
    because then one would decide and the other would look as though it did.
    """
    condition, requires = parse_condition("sma_20 > sma_50", _signal_names())
    return Strategy(
        name="baseline-event-driven",
        entry=EntryRule("sma_20 > sma_50", condition, requires),
        select=SelectionRule(right=Right.CALL, target_dte=30, dte_window=(21, 45),
                             target_delta=0.30),
        exit=ExitRule(hold_days=10, take_profit=1.0, dte_floor=5),
        components={"stop_loss": stop},
    )


def build_with_stop(stop: Bound) -> pd.DataFrame:
    """Run the configuration with an arbitrary form in slot 4.

    **Not the baseline.** This exists for the test that proves the recorded file is
    sensitive to the component's rule, which needs to run the same configuration with a
    different form. The baseline itself is :func:`build_event_driven_trades`, which takes
    no arguments, so there is exactly one thing it can produce.

    The engine's own execution settings are left alone -- no `max_concurrent`, capital
    enforced -- so the file records what an ordinary run does rather than a configuration
    chosen to make the comparison easy.
    """
    from backtest.api.sdk import Result

    inputs = event_driven_inputs()
    engine = EventDrivenEngine(inputs.market, inputs.chain, inputs.signals, inputs.config)
    result = Result(engine.run(event_driven_strategy(stop)))
    return result.trades[COMPARED]


def build_event_driven_trades() -> pd.DataFrame:
    """The recorded configuration. Fixed inputs and a fixed strategy, so the output
    should be bit-stable."""
    return build_with_stop(get("stop_loss", "trailing").bind(
        {"fraction": TRAILING_FRACTION}))


def read_recorded() -> pd.DataFrame:
    """The recorded file, with its dates back as `date` so it compares against a fresh
    run rather than against a reformatting of one."""
    if not EVENT_DRIVEN_FILE.exists():
        raise FileNotFoundError(
            f"no event-driven baseline at {EVENT_DRIVEN_FILE}; record one with "
            f"`python -m tools.baseline update --configuration {KEY}`")
    stored = pd.read_csv(EVENT_DRIVEN_FILE, parse_dates=["entry_date", "exit_date"])
    stored["entry_date"] = stored["entry_date"].dt.date
    stored["exit_date"] = stored["exit_date"].dt.date
    return stored


def difference(current: pd.DataFrame, stored: pd.DataFrame) -> pd.DataFrame | None:
    """The rows that moved, or None.

    The same rule as `tools.baseline.compare` -- a trade count first, then every compared
    column with the numbers rounded to 8 places. Written here rather than shared because
    the first configuration's reader is deliberately untouched by this change; if a third
    configuration arrives, factor the two together then.

    Rounded rather than exact because the alternative, comparing the file's text, would
    make a pandas release that changes a float's repr look like a change in results --
    which is the failure that teaches people to update a baseline without reading the diff.
    """
    if len(current) != len(stored):
        return pd.DataFrame({"issue": [f"trade count {len(stored)} -> {len(current)}"]})
    numeric = current.select_dtypes("number").columns
    moved = ~(
        current[numeric].round(8).eq(stored[numeric].round(8)).all(axis=1)
        & current.drop(columns=numeric).eq(stored.drop(columns=numeric)).all(axis=1)
    )
    return current.loc[moved] if moved.any() else None


def compare() -> pd.DataFrame | None:
    """Run the configuration and diff it against the recorded file."""
    return difference(build_event_driven_trades().reset_index(drop=True), read_recorded())
