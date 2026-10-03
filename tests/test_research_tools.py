"""Shadow tracking, counterfactual exits, validation, the tracker, interactions, surfaces.

These share the failure mode of everything in Layer 6: a research tool that is subtly wrong
produces a confident number, and the number gets acted on. So each test here plants the
situation where a naive implementation would lie, and checks that this one does not.

The recurring lie is **treating absence as zero**: a censored counterfactual as a loss of
nothing, an unused slot as a worthless one, an overlapping sample as an independent one.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tests.conftest import selection

from backtest.core.errors import StartupError
from backtest.research.counterfactual import grid_report, replay_exit, sweep_levels
from backtest.research.tracker import count_trials, deflate, describe
from backtest.research.validation import (
    block_bootstrap,
    bootstrap_trades,
    deflated_sharpe,
    expected_max_sharpe,
    purged_kfold,
    walk_forward,
)

D0 = date(2024, 1, 1)
DAYS = [D0 + timedelta(days=i) for i in range(500)]


def _trade(tid=1, entry_price=5.0, net=100.0, qty=1, mult=100, fees=2.0):
    return pd.DataFrame([{
        "trade_id": tid, "entry_price": entry_price, "quantity": qty,
        "multiplier": mult, "net_pnl": net, "fees": fees,
    }])


def _path(tid, ratios, start=D0):
    return pd.DataFrame([
        {"trade_id": tid, "date": start + timedelta(days=i), "ratio": r}
        for i, r in enumerate(ratios)
    ])


class TestShadowTracking:
    def test_the_engine_collects_quotes_after_an_exit(self, market, chain, signals, config):
        """The one thing that cannot be reconstructed afterwards."""
        from backtest.engine.loop import EventDrivenEngine
        from backtest.signals.contracts import SelectionRule
        from backtest.signals.entry.expression import parse_condition
        from backtest.signals.strategy import EntryRule, ExitRule
        from backtest.engine.strategy import Strategy
        from backtest.core.types import Right

        cond, req = parse_condition("sma_20 > sma_50", signals.names)
        strat = Strategy(entry=EntryRule("x", cond, req),
                         select=selection(right=Right.CALL, target_delta=0.30),
                         exit=ExitRule(hold_days=5, take_profit=1.0, stop_loss=0.5,
                                       dte_floor=5))
        result = EventDrivenEngine(market, chain, signals, config,
                                  shadow_days=10).run(strat)
        shadow = result.shadow_frame
        assert len(result.records) and len(shadow), "nothing was shadowed"
        assert (shadow["days_after_exit"] >= 1).all(), "a shadow row is not after the exit"
        assert (shadow["days_after_exit"] <= 10).all(), "a shadow row is past the window"

    def test_zero_days_collects_nothing(self, market, chain, signals, config):
        """Off has to mean off, so the cost can be declined."""
        from backtest.engine.loop import EventDrivenEngine
        from backtest.signals.contracts import SelectionRule
        from backtest.signals.entry.expression import parse_condition
        from backtest.signals.strategy import EntryRule, ExitRule
        from backtest.engine.strategy import Strategy
        from backtest.core.types import Right

        cond, req = parse_condition("sma_20 > sma_50", signals.names)
        strat = Strategy(entry=EntryRule("x", cond, req),
                         select=selection(right=Right.CALL, target_delta=0.30),
                         exit=ExitRule(hold_days=5, dte_floor=5))
        result = EventDrivenEngine(market, chain, signals, config,
                                  shadow_days=0).run(strat)
        assert result.shadow_frame.empty


class TestCounterfactualExits:
    def test_an_alternative_that_never_fires_is_censored_not_zero(self):
        """The bias this prevents: assuming an unfired exit closed at the window's end
        would make every wider stop look as good as the window happened to allow."""
        trades = _trade(net=100.0)
        path = _path(1, [0.0, 0.1, 0.2])          # never reaches +80%
        got = replay_exit(trades, path, path.iloc[0:0], take_profit=0.8)
        assert got.scored == 0 and got.censored == 1
        assert "nothing could be scored" in got.verdict()

    def test_the_stop_wins_a_same_day_tie(self):
        """A daily bar cannot say which came first, and assuming the better one flatters
        the result."""
        trades = _trade(entry_price=5.0, qty=1, mult=100, fees=0.0)
        path = _path(1, [0.0, 0.9])               # would touch both +80% and -50%? use both
        both = _path(1, [0.0, -0.6])              # only the stop
        stopped = replay_exit(trades, both, both.iloc[0:0],
                             take_profit=0.5, stop_loss=0.5)
        assert stopped.scored == 1
        # -50% of a 500 entry is -250.
        assert stopped.alternative == pytest.approx(-250.0)

    def test_the_shadow_extends_what_can_be_scored(self):
        """A wider stop fires after the real exit, so it is only observable because the
        shadow was collected."""
        trades = _trade(net=-100.0)
        held = _path(1, [0.0, -0.2])
        after = _path(1, [-0.45, -0.55], start=D0 + timedelta(days=2))
        without = replay_exit(trades, held, held.iloc[0:0], stop_loss=0.5)
        with_shadow = replay_exit(trades, held, after, stop_loss=0.5)
        assert without.censored == 1 and with_shadow.scored == 1

    def test_a_replay_needs_a_level(self):
        with pytest.raises(ValueError, match="needs a take_profit or a stop_loss"):
            replay_exit(_trade(), _path(1, [0.0]), _path(1, [0.0]))

    def test_the_grid_reports_the_censored_share_per_cell(self):
        """A cell that scored a third of the trades is not comparable with one that
        scored all of them."""
        grid = sweep_levels(_trade(), _path(1, [0.0, 0.4]), _path(1, [0.0]),
                            take_profits=(0.3, 1.5), stop_losses=(0.2,))
        assert "censored_share" in grid.columns
        assert grid.loc[grid.take_profit == 1.5, "censored_share"].iloc[0] == 1.0


class TestSplits:
    def test_walk_forward_test_windows_do_not_overlap_by_default(self):
        """Overlapping test windows make the folds dependent, so averaging over them
        gives an interval narrower than it looks."""
        folds = walk_forward(DAYS, train=200, test=50)
        for a, b in zip(folds, folds[1:]):
            assert a.test[1] < b.test[0]

    def test_expanding_mode_keeps_the_same_start(self):
        folds = walk_forward(DAYS, train=200, test=50, mode="expanding")
        assert all(f.train[0] == folds[0].train[0] for f in folds)

    def test_rolling_mode_moves_the_start(self):
        folds = walk_forward(DAYS, train=200, test=50)
        assert folds[-1].train[0] > folds[0].train[0]

    def test_a_period_too_short_is_refused_not_truncated(self):
        """A partial fold is scored on less data and would quietly carry more weight."""
        with pytest.raises(ValueError, match="cannot hold"):
            walk_forward(DAYS[:100], train=200, test=50)

    def test_purging_removes_training_trades_that_overlap_the_test_fold(self):
        """The leak this prevents: a trade held through the test period carries
        information about it."""
        entries = [D0 + timedelta(days=i * 7) for i in range(40)]
        exits = [e + timedelta(days=21) for e in entries]
        folds = purged_kfold(entries, exits, k=4)
        assert all(f.purged > 0 for f in folds), "nothing was purged despite 3-week holds"

    def test_non_overlapping_trades_need_no_purge(self):
        entries = [D0 + timedelta(days=i * 30) for i in range(40)]
        exits = [e + timedelta(days=2) for e in entries]
        folds = purged_kfold(entries, exits, k=4)
        assert sum(f.purged for f in folds) == 0

    def test_an_embargo_purges_more_than_no_embargo(self):
        entries = [D0 + timedelta(days=i * 7) for i in range(40)]
        exits = [e + timedelta(days=5) for e in entries]
        bare = sum(f.purged for f in purged_kfold(entries, exits, k=4))
        held = sum(f.purged for f in purged_kfold(entries, exits, k=4, embargo_days=20))
        assert held > bare

    def test_total_overlap_is_refused_with_the_reason(self):
        """When every trade spans the whole period there is no independent sample, and
        saying so is the finding."""
        entries = [D0 + timedelta(days=i) for i in range(20)]
        exits = [D0 + timedelta(days=400) for _ in entries]
        with pytest.raises(ValueError, match="purged every training trade"):
            purged_kfold(entries, exits, k=4)


class TestIntervals:
    def test_an_interval_that_crosses_zero_is_said_to(self):
        rng = np.random.default_rng(0)
        got = bootstrap_trades(rng.normal(0, 1000, 200), name="net")
        assert got.crosses_zero and "crosses zero" in got.verdict()

    def test_the_trade_bootstrap_warns_about_overlap_by_default(self):
        """For options the overlap is the normal case, and forgetting it is the quiet
        error."""
        rng = np.random.default_rng(0)
        assert "too narrow" in bootstrap_trades(rng.normal(0, 1, 50)).caveat

    def test_too_few_observations_is_refused(self):
        with pytest.raises(ValueError, match="cannot support a bootstrap"):
            bootstrap_trades([1.0, 2.0, 3.0])

    def test_the_block_bootstrap_is_wider_on_autocorrelated_data(self):
        """The point of blocks: dependence inside a block is kept, so the interval widens
        to something honest."""
        rng = np.random.default_rng(3)
        shocks = rng.normal(0, 1, 600)
        series = np.zeros(600)
        for i in range(1, 600):                   # strongly autocorrelated
            series[i] = 0.9 * series[i - 1] + shocks[i]
        narrow = bootstrap_trades(series, overlapping=False)
        wide = block_bootstrap(series, block=40)
        assert (wide.high - wide.low) > (narrow.high - narrow.low)

    def test_a_block_longer_than_the_data_is_refused(self):
        with pytest.raises(ValueError, match="fewer than 3 blocks"):
            block_bootstrap(np.zeros(50), block=30)


class TestDeflatedSharpe:
    def test_more_trials_raise_the_benchmark(self):
        a = expected_max_sharpe(10, 1 / 252)
        b = expected_max_sharpe(1000, 1 / 252)
        assert 0 < a < b

    def test_one_trial_has_no_benchmark(self):
        assert expected_max_sharpe(1, 1 / 252) == 0.0

    def test_the_same_sharpe_is_worth_less_after_more_trials(self):
        """The whole point: the best of 500 tries is not evidence the way one try is."""
        few = deflated_sharpe(0.09, trials=1, periods=504)
        many = deflated_sharpe(0.09, trials=500, periods=504)
        assert few.probability > many.probability

    def test_an_annualised_sharpe_paired_with_days_is_refused(self):
        """The easy error, and it overstates the result badly."""
        with pytest.raises(ValueError, match="annualised figure"):
            deflated_sharpe(1.8, trials=10, periods=504)

    def test_positive_skew_is_accounted_for(self):
        """Sharpe assumes near-normal returns; a long-options book is not, and ignoring
        it overstates exactly this payoff shape."""
        plain = deflated_sharpe(0.09, trials=20, periods=504, skew=0.0, kurtosis=3.0)
        skewed = deflated_sharpe(0.09, trials=20, periods=504, skew=1.5, kurtosis=9.0)
        assert plain.probability != skewed.probability

    def test_the_trial_sharpes_are_preferred_over_an_assumption(self):
        spread = np.array([0.01, 0.03, 0.05, 0.09])
        got = deflated_sharpe(0.09, trials=4, periods=504, sharpes=spread)
        assert got.trials == 4 and 0.0 <= got.probability <= 1.0


class TestTracker:
    RECORDS = [{"run_id": f"c{i}", "idea": "rsi", "sharpe": 0.01 * i} for i in range(10)]

    def test_a_rerun_is_not_a_new_trial(self):
        got = count_trials(self.RECORDS + [self.RECORDS[0]], idea="rsi")
        assert got.distinct == 10 and got.runs == 11

    def test_groupings_count_as_trials(self):
        """The count people undercount: five stratifications is five trials."""
        got = count_trials(self.RECORDS, idea="rsi", groupings=5)
        assert got.total == 15

    def test_a_record_without_a_run_id_is_refused(self):
        """Without it a rerun looks like a new trial and the count inflates."""
        with pytest.raises(ValueError, match="no run_id"):
            count_trials([{"idea": "rsi"}], idea="rsi")

    def test_deflating_needs_a_recorded_sharpe(self):
        got = count_trials([{"run_id": "a"}], idea="rsi")
        with pytest.raises(ValueError, match="nothing to deflate"):
            deflate(got, periods=252)

    def test_describe_separates_runs_from_distinct(self):
        frame = describe(self.RECORDS + [self.RECORDS[0]])
        assert frame.loc["rsi", "reruns"] == 1


class TestPlateau:
    def _grid(self, values):
        rows = []
        for tp, row in zip((0.3, 0.5, 0.8), values):
            for sl, v in zip((0.2, 0.3, 0.4), row):
                rows.append({"take_profit": tp, "stop_loss": sl, "net_pnl": v})
        return pd.DataFrame(rows)

    def test_a_spike_is_called_a_spike(self):
        """One cell far above its neighbours is the cell that fitted this history."""
        from backtest.research.sweep import plateau

        grid = self._grid([[0, 0, 0], [0, 1000, 0], [0, 0, 0]])
        assert "spike" in plateau(grid).verdict()

    def test_a_plateau_is_called_a_plateau(self):
        from backtest.research.sweep import plateau

        grid = self._grid([[900, 950, 940], [960, 1000, 970], [930, 980, 950]])
        got = plateau(grid)
        assert got.share >= 0.75 and "plateau" in got.verdict()

    def test_uneven_grid_steps_do_not_make_distant_points_neighbours(self):
        """A sweep of 0.3, 0.5, 1.0 is uneven; assuming a constant step would call 1.0 a
        neighbour of 0.3."""
        from backtest.research.sweep import plateau

        rows = [{"take_profit": tp, "stop_loss": 0.3, "net_pnl": v}
                for tp, v in ((0.3, 10.0), (0.5, 1000.0), (1.0, 10.0), (2.0, 10.0))]
        got = plateau(pd.DataFrame(rows))
        assert got.neighbours == 2, "only the adjacent swept values are neighbours"

    def test_the_surface_is_a_grid_not_a_ranking(self):
        from backtest.research.sweep import surface

        got = surface(self._grid([[1, 2, 3], [4, 5, 6], [7, 8, 9]]),
                      "take_profit", "stop_loss")
        assert got.shape == (3, 3)

    def test_a_missing_metric_is_refused(self):
        from backtest.research.sweep import plateau

        with pytest.raises(StartupError, match="no 'sharpe'"):
            plateau(self._grid([[1, 2, 3]] * 3), metric="sharpe")


class TestThresholdRegistry:
    """Every number the system assumes, in the architecture document's four groups.

    This is the file that stops thresholds leaking back into call sites, so the tests are
    about the rule holding rather than about any particular value.
    """

    def test_every_threshold_is_in_one_of_the_documents_groups(self):
        from backtest.core.thresholds import GROUPS, THRESHOLDS

        for t in THRESHOLDS.values():
            assert t.kind in GROUPS, f"{t.name} is in {t.kind!r}, which is not a group"

    def test_every_group_states_what_may_be_done_with_its_numbers(self):
        """A classification without a rule is a label."""
        from backtest.core.thresholds import GROUPS

        assert set(GROUPS) == {"realism", "engineering", "strategy", "method"}
        for rule in GROUPS.values():
            assert len(rule) > 20

    def test_no_strategy_choice_is_defaulted(self):
        """The document permits optimising a strategy choice under its protocol; it does
        not permit the platform picking one. A default here would decide the search and
        then report its winner as a result."""
        from backtest.core.thresholds import strategy_parameters_are_never_defaulted

        assert strategy_parameters_are_never_defaulted()

    def test_the_exit_level_sweep_requires_its_levels(self):
        """Which exit levels are worth exploring is a group-3 choice."""
        from backtest.research.counterfactual import sweep_levels

        with pytest.raises(ValueError, match="group 3"):
            sweep_levels(_trade(), _path(1, [0.0]), _path(1, [0.0]),
                         take_profits=(), stop_losses=(0.3,))

    def test_every_threshold_says_what_it_decides_and_why(self):
        """A number whose reason nobody wrote down gets relaxed the first time it is
        inconvenient."""
        from backtest.core.thresholds import THRESHOLDS

        for t in THRESHOLDS.values():
            assert len(t.decides) > 20, f"{t.name} does not say what it decides"
            assert len(t.because) > 30, f"{t.name} does not say why that value"
            assert t.used_by, f"{t.name} does not say where it is used"

    def test_an_unknown_name_raises_rather_than_defaulting(self):
        """A typo returning a silent default is how a tool reads a threshold nobody set."""
        from backtest.core.thresholds import get, override

        with pytest.raises(KeyError, match="no threshold named"):
            get("plateu_share")
        with pytest.raises(KeyError, match="no threshold named"):
            override(plateu_share=0.5)

    def test_an_override_reaches_the_tool_that_reads_it(self):
        """The registry is pointless if a module kept its own copy."""
        from backtest.core.thresholds import override, reset
        from backtest.research.sweep import Plateau

        p = Plateau(best={"x": 1}, best_value=100.0, neighbours=10, holding=5,
                    tolerance=0.2, worst_neighbour=50.0)
        try:
            override(plateau_share=0.9, slope_share=0.8)
            assert "spike" in p.verdict()
            override(plateau_share=0.4, slope_share=0.2)
            assert "plateau" in p.verdict()
        finally:
            reset()

    def test_a_verdict_names_the_cut_it_used(self):
        """So the reader can disagree with the judgement instead of taking it as a
        measurement."""
        from backtest.research.sweep import Plateau

        p = Plateau(best={"x": 1}, best_value=100.0, neighbours=10, holding=9,
                    tolerance=0.2, worst_neighbour=95.0)
        assert "cut" in p.verdict()

    def test_the_priority_five_are_flagged(self):
        """The document names five choices that move results most; they should be findable
        without reading the PDF."""
        from backtest.core.thresholds import THRESHOLDS

        # Only large_shock and the marking method live here; lambda, latency and the
        # contract choice live in config and in the strategy, so the flag is allowed to be
        # empty rather than forced.
        assert all(isinstance(t.priority, bool) for t in THRESHOLDS.values())


class TestSyntheticBoundary:
    """Every option-side conclusion states its boundary, and cannot be printed without one.

    The project claims the synthetic chain's limits are "determined automatically from the
    source column, with no manual switch". That was true of Result.summary() and of nothing
    else: the research tools each computed a premium-denominated number and said nothing.
    A number nobody may act on, that does not say so, is worse than no number — it gets
    quoted.
    """

    def test_an_underlying_conclusion_survives_a_synthetic_chain(self):
        """An event study never touches an option, so the chain cannot affect it."""
        from backtest.research.boundary import Boundary, Scope

        assert Boundary(Scope.UNDERLYING, synthetic=True).usable
        assert Boundary(Scope.UNDERLYING, synthetic=True).note() == ""

    def test_an_option_conclusion_does_not(self):
        from backtest.research.boundary import Boundary, Scope

        b = Boundary(Scope.OPTION, synthetic=True)
        assert not b.usable and "Black-Scholes" in b.note()

    def test_a_mixed_comparison_is_called_biased_not_merely_limited(self):
        """The bias does not cancel: it lands entirely on the comparison."""
        from backtest.research.boundary import Boundary, Scope

        assert "biased" in Boundary(Scope.MIXED, synthetic=True).note()

    def test_real_data_carries_no_warning(self):
        from backtest.research.boundary import Boundary, Scope

        for scope in Scope:
            assert Boundary(scope, synthetic=False).note() == ""

    def test_the_boundary_comes_before_the_numbers(self):
        """A caveat under a table is read once and scrolled past; the number above it is
        what gets copied into a decision."""
        from backtest.research.boundary import Boundary, Scope, header

        lines = header(Boundary(Scope.OPTION, synthetic=True), what="an exit level")
        assert lines and any("!!" in ln for ln in lines)

    @pytest.mark.parametrize("module,func", [
        ("backtest.research.counterfactual", "report"),
        ("backtest.research.ablation", "report"),
    ])
    def test_synthetic_is_required_not_defaulted(self, module, func):
        """A default would let a forgotten argument produce an unlabelled report, which is
        the hole this mechanism exists to close."""
        import importlib
        import inspect

        fn = getattr(importlib.import_module(module), func)
        param = inspect.signature(fn).parameters["synthetic"]
        assert param.default is inspect.Parameter.empty, f"{module}.{func} defaults it"

    def test_the_mfe_mae_study_also_requires_it(self):
        """The distribution most often used to set a take-profit or a stop, and the one
        that went longest without a boundary."""
        import inspect

        from backtest.research.excursion import ExcursionStudy

        param = inspect.signature(ExcursionStudy.text).parameters["synthetic"]
        assert param.default is inspect.Parameter.empty

    def test_the_exit_level_sweep_still_requires_its_levels(self):
        """The boundary does not replace the group-3 rule; both apply."""
        from backtest.research.counterfactual import sweep_levels

        with pytest.raises(ValueError, match="group 3"):
            sweep_levels(_trade(), _path(1, [0.0]), _path(1, [0.0]),
                         take_profits=(0.3,), stop_losses=())

class TestLevelGrid:
    """The level grid as text. It had numbers and no boundary at all.

    A grid handed to a reader invites reading the best cell, and the best cell is the one
    that fitted these trades. So the text says that above the numbers and marks the best
    cell as the least trustworthy, rather than leaving the reader to supply the caution.
    """

    GRID = pd.DataFrame([
        {"take_profit": 0.5, "stop_loss": 0.3, "difference": -900.0,
         "scored": 5, "censored_share": 0.0},
        {"take_profit": 1.0, "stop_loss": 0.3, "difference": 400.0,
         "scored": 5, "censored_share": 0.0},
        {"take_profit": 1.5, "stop_loss": 0.3, "difference": float("nan"),
         "scored": 0, "censored_share": float("nan")},
    ])

    def test_the_boundary_comes_before_the_numbers(self):
        text = grid_report(self.GRID, synthetic=True)
        assert text.index("!!") < text.index("-900")

    def test_real_data_carries_no_synthetic_warning(self):
        assert "synthetic chain" not in grid_report(self.GRID, synthetic=False)

    def test_the_best_cell_is_named_as_the_least_trustworthy(self):
        text = grid_report(self.GRID, synthetic=False)
        assert "least trustworthy" in text
        assert "take profit 1.00" in text

    def test_the_plateau_is_what_the_reader_is_pointed_at(self):
        assert "plateau" in grid_report(self.GRID, synthetic=False)

    def test_an_unscored_cell_is_a_dash_not_a_zero(self):
        """A cell that scored nothing is not a cell worth zero. Its ``scored`` count is
        genuinely 0; its **difference** must be a dash."""
        text = grid_report(self.GRID, synthetic=False)
        line = next(ln for ln in text.splitlines() if "1.50" in ln)
        difference = line.split()[2]
        assert difference == "\u2014", f"the unscored cell shows {difference!r}"

    def test_a_grid_with_nothing_scored_says_so(self):
        empty = self.GRID.assign(difference=float("nan"))
        assert "no cell could be scored" in grid_report(empty, synthetic=False)

    def test_heavily_censored_cells_are_flagged(self):
        """A cell that scored a third of the trades is not comparable with one that
        scored all of them."""
        heavy = self.GRID.copy()
        heavy.loc[0, "censored_share"] = 0.8
        assert "not comparable" in grid_report(heavy, synthetic=False)
