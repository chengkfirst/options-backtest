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
         "TestAPositionOpenAtTheEnd::"
         "test_a_date_beyond_the_calendar_raises_rather_than_clamping",),
    ),
    Mutation(
        "the calendar runs past the bars",
        "backtest/data/market.py",
        "CALENDAR_MARGIN_DAYS = 830",
        "CALENDAR_MARGIN_DAYS = 0",
        ("tests/test_engine.py::"
         "TestAPositionOpenAtTheEnd::"
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
        'LEDGER_WRITES = ("apply_fill", "settle_expiry", "liquidate_stock",',
        'LEDGER_WRITES = ("apply_fill",)  # (',
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
        ("tests/test_adapters.py::TestChecker",),
    ),
    Mutation(
        "a locked market is flagged",
        "backtest/data/options/adapters.py",
        "    if locked.any():",
        "    if False:",
        ("tests/test_adapters.py::TestChecker",),
    ),
    Mutation(
        # Reading the root off the tree gives the max of holding and exercising, so
        # comparing the intrinsic with it is comparing it with itself.
        "the hold value is the step-1 nodes, not the root",
        "backtest/data/options/american.py",
        "    hold = discount * (p * values[0] + (1 - p) * values[1])",
        "    hold = values[0]",
        ("tests/test_american.py::TestTheImpossibilities",
         "tests/test_american.py::TestWhatTheoryRequires"),
    ),
    Mutation(
        "an American value is the greater of holding and exercising",
        "backtest/data/options/american.py",
        "        american=max(hold, intrinsic),",
        "        american=hold,",
        ("tests/test_american.py::TestWhatTheoryRequires",),
    ),
    Mutation(
        "a node takes the greater of holding and exercising",
        "backtest/data/options/american.py",
        "            values[i] = max(held, _intrinsic(node_spot, strike, right))",
        "            values[i] = held",
        ("tests/test_american.py::TestWhatTheoryRequires",),
    ),
    Mutation(
        # The flag's job is to over-select. A tree run on every position every session
        # would be minutes rather than seconds.
        "the flag screens before the tree is built",
        "backtest/engine/loop.py",
        '        if "early_exercise_value" not in str(row.get("quality_flags", "")):',
        "        if False:",
        ("tests/test_early_exercise.py::TestTheFlagScreensAndTheTreeDecides",),
    ),
    Mutation(
        "the tree can overrule the flag",
        "backtest/engine/loop.py",
        "        if not verdict.exercise_now:",
        "        if False:",
        ("tests/test_early_exercise.py::TestTheFlagScreensAndTheTreeDecides",),
    ),
    Mutation(
        # The cost section listed three fees and totalled them, for a book whose largest
        # cost is the spread and is not a fee.
        "the spread is part of the cost total",
        "backtest/research/metrics.py",
        "    total = fee_total + (spread_dollars if spread_known else 0.0)",
        "    total = fee_total",
        ("tests/test_cost_truth.py::TestTheSpreadIsInTheTotal",
         "tests/test_cost_truth.py::TestTheAnomalyRuleCanSeeIt"),
    ),
    Mutation(
        # `total / abs(gross)` on a losing run is "fees as a share of the loss", printed
        # as "% of gross".
        "a losing run gets no cost-to-gross ratio",
        "backtest/research/metrics.py",
        "    if gross_at_mid > 0:",
        "    if True:",
        ("tests/test_cost_truth.py::TestTheDenominator",),
    ),
    Mutation(
        # The rule read fees_pct_of_gross, so the spread could never be in the numerator.
        "the cost rule measures the edge before costs",
        "backtest/research/anomaly.py",
        '    pct = costs.get("cost_pct_of_gross_at_mid")',
        '    pct = costs.get("fees_pct_of_premium")',
        ("tests/test_cost_truth.py::TestTheAnomalyRuleCanSeeIt",),
    ),
    Mutation(
        # The ledger keeps interest as its own term and the report folded it back in.
        "the strategy's return is reported apart from the account's",
        "backtest/accounting/tradebook.py",
        '                    "strategy_equity": s.equity_mid - s.interest,',
        '                    "strategy_equity": s.equity_mid,',
        ("tests/test_return_separation.py::TestTheEquityFrameCarriesWhatSeparatesThem",
         "tests/test_return_separation.py::TestThreeReturnsWithThreeNames"),
    ),
    Mutation(
        # The risk-free rate was added once through the interest and subtracted never.
        "sharpe is on the excess return",
        "backtest/api/sdk.py",
        "        sharpe = excess_annual / vol if vol and np.isfinite(vol) else float(\"nan\")",
        "        sharpe = annual / vol if vol and np.isfinite(vol) else float(\"nan\")",
        ("tests/test_return_separation.py::TestThreeReturnsWithThreeNames",),
    ),
    Mutation(
        # Paying the full Treasury rate on every dollar of cash is a claim about the
        # broker, and it runs in the flattering direction.
        "the share of the rate the cash earns is an assumption, not 1.0 in the code",
        "backtest/accounting/ledger.py",
        '        rate = rate * float(get("cash_interest_share"))',
        "        rate = rate * 1.0",
        ("tests/test_return_separation.py::TestTheCashRateIsAnAssumption",),
    ),
    Mutation(
        # A signed total as the denominator reverses every term's sign.
        "an attribution share is of the movement, not of the signed total",
        "backtest/accounting/attribution.py",
        "            \"share\": {k: (v / self.magnitude if self.magnitude else float(\"nan\"))",
        "            \"share\": {k: (v / gross if gross else float(\"nan\"))",
        ("tests/test_attribution_daily.py::TestTheSharesAreMagnitudes",),
    ),
    Mutation(
        # Without the spread as its own term it sits in the residual, where it cannot be
        # told from the expansion failing.
        "the spread is an attribution term rather than part of the residual",
        "backtest/accounting/attribution.py",
        '        terms["spread"] = -(',
        '        terms["spread"] = 0.0 * -(',
        ("tests/test_attribution_daily.py::TestTheSpreadIsItsOwnTerm",),
    ),
    Mutation(
        # The exposure limit had unit tests, a live call site, and no way in.
        "the exposure limit can be set from run()",
        "backtest/api/sdk.py",
        "            limits=_exposure_from(exposure),",
        "            limits=None,",
        ("tests/test_reachable.py::TestTheExposureLimitActuallyBinds",),
    ),
    Mutation(
        # Two runs that cleaned the data differently shared one id, so a sensitivity
        # test over cleaning -- which the register asks for by name -- read as one run
        # repeated.
        "the cleaning rule set reaches the run fingerprint",
        "backtest/api/sdk.py",
        '            data_version += " clean:" + str(cleaning.version())',
        "            pass",
        ("tests/test_cleaning_fingerprint.py::TestTheRuleSetReachesTheRunId",),
    ),
    Mutation(
        # It accepted cleaning rules and applied none of them, raising nothing.
        "the synthetic chain refuses options it would ignore",
        "backtest/data/options/chain_registry.py",
        '    unknown = sorted(k for k in (opts or {}) if k != "kind")',
        "    unknown = []",
        ("tests/test_cleaning_fingerprint.py::TestTheSyntheticChainRefusesWhatItCannotDo",),
    ),
    Mutation(
        # Delta and vega were limited and premium at risk was not, which is the one
        # whose breach is a certainty rather than a scenario.
        "the premium budget blocks an entry that would cross it",
        "backtest/engine/loop.py",
        "        if self.budget is not None and self.budget.active:",
        "        if False:",
        ("tests/test_premium_budget.py::TestTheEngineReadsIt",),
    ),
    Mutation(
        # 10% of equity on one expiry and 10% spread over six are the same total and
        # different books. Only the per-expiry limit can tell them apart.
        "a concentration on one expiry is refused even inside the total budget",
        "backtest/portfolio/constraints.py",
        "            if after > self.max_premium_per_expiry_pct:",
        "            if False:",
        ("tests/test_premium_budget.py::TestThePerExpiryBudget",),
    ),
    Mutation(
        # The fill was recorded and the quote it came from was discarded, which made the
        # spread paid uncomputable and lambda unauditable per trade.
        "the quote is recorded beside the fill",
        "backtest/accounting/tradebook.py",
        '        "mid": mid,',
        "",
        ("tests/test_options_metrics.py::TestOnARealRun",),
    ),
    Mutation(
        # A realised vol of 0 reads as the most profitable possible trade for a buyer.
        "a holding window too short to measure is excluded, not zeroed",
        "backtest/research/metrics.py",
        "        if len(returns) < min_returns:",
        "        if False:",
        ("tests/test_options_metrics.py::TestImpliedAgainstRealized",),
    ),
    Mutation(
        # It reported "the requested range is covered" for a 2030 request against data
        # ending in 2023, and seven of nine steps then passed on it.
        "a range the data does not reach fails the source check",
        "backtest/data/pipeline.py",
        "        FAIL if disjoint else (WARN if notes else PASS),",
        "        PASS,",
        ("tests/test_check_data.py::TestWhatItSaysAboutDataItDoesNotHave",),
    ),
    Mutation(
        # A zero dividend biases every in-the-money call's IV, and the rows it is worst
        # on are the ones early exercise turns on.
        "IV is solved at the market's own dividend yield",
        "backtest/data/options/adapters.py",
        "                self.market.dividend_yield(a, e, float(sp))",
        "                0.0",
        ("tests/test_adapters.py::TestWhichIVModelSolvedIt",),
    ),
    Mutation(
        # Bisection on a flat function returns wherever it stopped: 0.1631 for a
        # contract priced at a true 0.25.
        "a price at the intrinsic has no implied volatility",
        "backtest/data/options/american.py",
        "    if abs(price - intrinsic) < max(tolerance, intrinsic * 1e-6):",
        "    if False:",
        ("tests/test_american.py::TestTheAmericanIVSolve",),
    ),
    Mutation(
        "which IV model solved a row is on the row",
        "backtest/data/options/adapters.py",
        '                "|iv_american" if use_american else "|iv_european")',
        '                "")',
        ("tests/test_adapters.py::TestWhichIVModelSolvedIt",),
    ),
    Mutation(
        # One constant λ prices the easiest fill in the book the same as the hardest.
        "λ widens near expiry",
        "backtest/execution/costs.py",
        "    if (costs.near_expiry_days > 0 and days_to_expiry is not None",
        "    if (False and days_to_expiry is not None",
        ("tests/test_selection_costs.py::TestLambdaWidensWhereAFillIsHarder",),
    ),
    Mutation(
        "λ widens deep out of the money",
        "backtest/execution/costs.py",
        "    if (costs.deep_otm_delta > 0.0 and delta is not None",
        "    if (False and delta is not None",
        ("tests/test_selection_costs.py::TestLambdaWidensWhereAFillIsHarder",),
    ),
    Mutation(
        # Two multipliers compound into a number nobody chose; two additions are each
        # readable in the result as the thing that was set.
        "the two widenings add rather than multiply",
        "backtest/execution/costs.py",
        "        out += costs.deep_otm_extra",
        "        out *= 1.0 + costs.deep_otm_extra",
        ("tests/test_selection_costs.py::TestLambdaWidensWhereAFillIsHarder",),
    ),
    Mutation(
        # Folded into realised, a losing strategy reads as a profitable one: over three
        # years the interest is twenty times the fixture strategy's net P&L.
        "interest is not the strategy's P&L",
        "backtest/accounting/ledger.py",
        "        self.total_interest += earned",
        "        self.realised += earned",
        ("tests/test_cash_and_interest.py::TestInterestIsNotTheStrategysPnL",),
    ),
    Mutation(
        "interest is a term of the accounting identity",
        "backtest/accounting/ledger.py",
        "            + unreal_stock + self.total_interest + self.total_dividends",
        "            + unreal_stock + self.total_dividends",
        ("tests/test_cash_and_interest.py::TestInterestIsNotTheStrategysPnL",),
    ),
    Mutation(
        "a dividend reaches shares and not options",
        "backtest/accounting/ledger.py",
        "        position = self._stock.get(symbol)\n        if position is None or per_share <= 0:",
        "        position = self._positions.get(symbol)\n        if position is None or per_share <= 0:",
        ("tests/test_cash_and_interest.py::TestDividendsReachCashOnlyForShares",),
    ),
    Mutation(
        # The refusal has to happen when the order is placed, which is where a real
        # account refuses it.
        "cash is refused at the order, not at the fill",
        "backtest/accounting/ledger.py",
        "        if amount > self.available + 1e-9:",
        "        if False:",
        ("tests/test_cash_and_interest.py::TestFrozenCashRefusesAtTheOrder",),
    ),
    Mutation(
        "a position cannot be sold on the day it opened",
        "backtest/accounting/ledger.py",
        "        return 0 if position.opened >= when else position.abs_quantity",
        "        return position.abs_quantity",
        ("tests/test_cash_and_interest.py::TestTheSettlementRule",),
    ),
    Mutation(
        "the ledger notices an entry going missing",
        "backtest/accounting/ledger.py",
        "        if len(self.events) != self._sealed:",
        "        if False:",
        ("tests/test_ledger.py",),
    ),
    Mutation(
        # A flag nothing reads is a cleaning rule that reports and does not clean -- the
        # exact state `check_frame` was in before this stage existed.
        "a row a cleaning rule marked untradable is not filled",
        "backtest/execution/costs.py",
        "    if flags and UNTRADABLE_FLAG in str(flags):",
        "    if False:",
        ("tests/test_clean.py::TestTheMarkRefusesAFill",),
    ),
    Mutation(
        "no cleaning rule is on by default",
        "backtest/data/options/clean.py",
        "        if not self.rules:",
        "        if False:",
        ("tests/test_clean.py::TestWhatTheStageRefusesToDo",),
    ),
    Mutation(
        # Double-counting would make a rule set look like it removed more than it did.
        "a row two rules match is attributed to the first",
        "backtest/data/options/clean.py",
        "            fresh = hit & ~handled",
        "            fresh = hit",
        ("tests/test_clean.py::TestTheReport",),
    ),
    Mutation(
        # A pause that lets the loop continue is a second name for no_new_positions.
        "pause stops the run",
        "backtest/engine/loop.py",
        "            if self._risk.action is RiskAction.PAUSE:",
        "            if False:",
        ("tests/test_pause.py::TestAPausedRunSaysSo",),
    ),
    Mutation(
        "a paused run carries a checkpoint",
        "backtest/engine/loop.py",
        "                if (until is not None or paused_on is not None) else None),",
        "                if until is not None else None),",
        ("tests/test_pause.py::TestAPausedRunSaysSo",
         "tests/test_pause.py::TestResumingAPausedRun"),
    ),
    Mutation(
        # A reference rather than a copy shows a trade closed in February a regime
        # from June.
        "the candidate's record is copied into the trade that opens",
        "backtest/engine/loop.py",
        "                born = born.opened_from(dict(from_candidate.of(\"setup\")))",
        "                pass  # born = born.opened_from(...)",
        ("tests/test_trade_context.py::TestTheCandidateAndEntrySlots",),
    ),
    Mutation(
        # A buy placed before the sell that pays for it is a rejected order in a cash
        # account, on a day the strategy intended to trade.
        "sells are ordered before buys",
        "backtest/portfolio/target.py",
        "        return tuple(sorted(out, key=lambda o: (o.quantity > 0, str(o.contract))))",
        "        return tuple(out)",
        ("tests/test_target.py::TestTheDiff",),
    ),
    Mutation(
        "the scenario limit is read before an entry is placed",
        "backtest/engine/loop.py",
        "        if self.scenario_limit is not None:",
        "        if False:",
        ("tests/test_scenario_rule.py::TestTheEngineReadsIt",),
    ),
    Mutation(
        # Every comparison with a missing value is false, so the scenario a limit is
        # most wanted for would be the one it silently ignored.
        "an unpriced scenario is a breach, not a pass",
        "backtest/portfolio/scenario_rule.py",
        "        if math.isnan(change):",
        "        if False:",
        ("tests/test_scenario_rule.py::TestTheUnknownCellIsNotAPass",),
    ),
    Mutation(
        "a limit over a shock the grid cannot price is refused",
        "backtest/portfolio/scenario_rule.py",
        "        if beyond:",
        "        if False:",
        ("tests/test_scenario_rule.py::TestTheUnknownCellIsNotAPass",),
    ),
    Mutation(
        # The omission the equality test found: an order in flight names the ORDER event
        # it was published as, and a fresh bus hands out ids from 1.
        "a resumed run's event ids follow on from the first part's",
        "backtest/engine/loop.py",
        "            self.bus.resume(held.events, held.next_event_id)",
        "            pass  # self.bus.resume(...)",
        ("tests/test_runner.py::"
         "TestAResumedRunIsTheSameRun::test_the_event_stream_follows_on",),
    ),
    Mutation(
        "a resumed run restores the equity counters rather than restarting them",
        "backtest/engine/loop.py",
        '        self._peak_equity = counters.get("peak_equity", start_equity)',
        "        self._peak_equity = start_equity",
        ("tests/test_runner.py::"
         "TestAResumedRunIsTheSameRun::"
         "test_the_equity_counters_are_restored_and_not_restarted",),
    ),
    Mutation(
        "a resumed run restores every stateful component",
        "backtest/engine/loop.py",
        "                component.set_state(state)",
        "                pass  # component.set_state(state)",
        ("tests/test_runner.py::TestAResumedRunIsTheSameRun",),
    ),
    Mutation(
        "a batch counts the configurations that failed as trials",
        "backtest/engine/runner.py",
        "        nobody wrote down.\n        \"\"\"\n        return len(self.entries)",
        "        nobody wrote down.\n        \"\"\"\n"
        "        return len([e for e in self.entries if e.ran])",
        ("tests/test_runner.py::TestTheBatchRunner",),
    ),
    Mutation(
        # Execution reading a quantity decided somewhere else is the engine going back to
        # turning a decision straight into an order, which produces the same result on
        # any day where no two slots overlap.
        "an order's quantity is the gap the target named",
        "backtest/portfolio/target.py",
        "            out.append(Order(contract=contract, quantity=target - have, held=have,",
        "            out.append(Order(contract=contract, quantity=-have, held=have,",
        ("tests/test_target.py::TestTheDiff",
         "tests/test_target.py::TestTheEngineUsesIt"),
    ),
    Mutation(
        "two slots wanting one contract compose instead of racing",
        "backtest/portfolio/target.py",
        "        self._reasons.setdefault(adjustment.contract, []).append(adjustment)",
        "        self._reasons[adjustment.contract] = [adjustment]",
        ("tests/test_target.py::TestTwoSlotsWantingOneContract",),
    ),
    Mutation(
        "a contract no slot looked at is left alone",
        "backtest/portfolio/target.py",
        "            if target == have:\n                continue",
        "            if False:\n                continue",
        ("tests/test_target.py::TestTheDiff",),
    ),
    Mutation(
        # The mark not surviving between days turns the trailing stop into a fixed one
        # at the same fraction: a plausible result from a strategy nobody wrote.
        "a stateful form's state survives between days",
        "backtest/core/component.py",
        "        if trade_id not in self._state:\n"
        "            self._state[trade_id] = dict(self.variant.initial_state(ctx))\n"
        "        return self._state[trade_id]",
        "        return dict(self.variant.initial_state(ctx))",
        ("tests/test_component_state.py::TestTheTrailingStop",
         "tests/test_component_state.py::TestTheEngineUsesIt"),
    ),
    Mutation(
        "a stateful form's state is per trade, not shared",
        "backtest/core/component.py",
        "        trade_id = ctx.get(\"trade_id\")",
        "        trade_id = 0",
        ("tests/test_component_state.py::TestTheStateIsPerTrade",),
    ),
    Mutation(
        "a snapshot's keys survive a round trip through JSON",
        "backtest/core/component.py",
        "        return {str(trade): dict(state) for trade, state in self._state.items()}",
        "        return {trade: dict(state) for trade, state in self._state.items()}",
        ("tests/test_component_state.py::TestTheSnapshot",),
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

    # **The named tests have to exist and pass first.**
    #
    # A renamed test leaves the entry pointing at an id pytest cannot collect, and pytest
    # exits non-zero for that -- which this read as "the mutation was caught". So a
    # mechanism whose test had been renamed reported OK for ever, which is the exact
    # failure this whole file exists to prevent, one level up. Found when a test renamed
    # minutes earlier still reported its mutation as caught.
    for target in mutation.tests:
        if target.endswith(".py"):
            continue
        probe = subprocess.run(
            [sys.executable, "-m", "pytest", target, "-q", "--no-header",
             "--collect-only"],
            cwd=ROOT, capture_output=True)
        if probe.returncode != 0:
            return False, f"{target} cannot be collected; the entry names no such test"

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
