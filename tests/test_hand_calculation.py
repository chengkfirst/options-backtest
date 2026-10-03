"""The architecture document's own two acceptance criteria, worked by hand.

The document's build order names exactly two, and both are a hand calculation:

    Stage 1, minimal loop -- "Done when buy-and-hold matches a hand calculation."
    Stage 3, options data and execution -- "Done when a single long call held to expiry
    matches a hand calculation, including fees and settlement."

Neither existed. The stages were being worked past while their stated completion test had
never been written, which is how a system comes to be "mostly done" at every stage and
finished at none.

## Why a hand calculation and not another test

Every other test in this suite checks the engine against **itself**: the two engines agree,
the invariants hold, the baseline has not moved. All of those pass perfectly on a system
that is consistently wrong. A number worked out on paper is the only test here that is
computed somewhere other than in this codebase, and it is the only one that can catch a
formula that has been wrong since the day it was written.

So the arithmetic below is written out in full, in the comments, with the numbers a person
can check. **Do not replace it with a call to the function being tested.** That would make
it pass by construction and quietly delete the only independent check in the project.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.core.config import CostConfig
from backtest.core.types import Right, Side
from backtest.execution.costs import execute, fill_price, order_fees

from tests.kinds import labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("known_answer", "4")

#: The cost model these calculations use. Stated here rather than taken from the defaults,
#: so the arithmetic below stays true when a default moves.
COSTS = CostConfig(
    adverse_fraction=1.0,            # pay the ask, receive the bid
    commission_per_contract=0.65,
    min_commission_per_order=1.00,
    regulatory_fee_per_contract=0.05,
    slippage_per_contract=0.0,
)
MULTIPLIER = 100


class TestFillPrice:
    """The document's 4.1: fill = mid + lambda x (ask - mid), lambda >= 1."""

    @pytest.mark.req("4.1.lambda")
    def test_a_buy_at_lambda_one_pays_the_ask(self):
        # bid 4.80, ask 5.20 -> mid 5.00, half-spread 0.20.
        # buy at lambda 1:  5.00 + 1.0 x 0.20 = 5.20, which is the ask.
        assert fill_price(4.80, 5.20, Side.BUY, COSTS) == pytest.approx(5.20)

    @pytest.mark.req("0.4.fills")
    def test_a_sell_at_lambda_one_receives_the_bid(self):
        # sell at lambda 1:  5.00 - 1.0 x 0.20 = 4.80, which is the bid.
        assert fill_price(4.80, 5.20, Side.SELL, COSTS) == pytest.approx(4.80)

    def test_lambda_above_one_is_worse_than_the_ask(self):
        # The document's sensitivity test. lambda 1.5: 5.00 + 1.5 x 0.20 = 5.30,
        # ten cents through the ask.
        worse = CostConfig(adverse_fraction=1.5)
        assert fill_price(4.80, 5.20, Side.BUY, worse) == pytest.approx(5.30)

    def test_lambda_zero_is_the_mid(self):
        mid_only = CostConfig(adverse_fraction=0.0)
        assert fill_price(4.80, 5.20, Side.BUY, mid_only) == pytest.approx(5.00)


class TestFees:
    def test_three_contracts(self):
        # commission: 3 x 0.65 = 1.95, above the 1.00 order minimum, so 1.95
        # regulatory: 3 x 0.05 = 0.15
        # slippage:   0
        fees = order_fees(3, COSTS, MULTIPLIER)
        assert fees.commission == pytest.approx(1.95)
        assert fees.regulatory == pytest.approx(0.15)
        assert fees.total == pytest.approx(2.10)

    def test_one_contract_hits_the_order_minimum(self):
        # commission: 1 x 0.65 = 0.65, below the 1.00 minimum, so 1.00
        # regulatory: 1 x 0.05 = 0.05
        fees = order_fees(1, COSTS, MULTIPLIER)
        assert fees.commission == pytest.approx(1.00)
        assert fees.total == pytest.approx(1.05)

    @pytest.mark.req("4.2.fees")
    def test_the_parts_sum_to_the_total(self):
        """The identity the trade-record invariant also checks, at the other end."""
        fees = order_fees(7, COSTS, MULTIPLIER)
        assert fees.total == pytest.approx(
            fees.commission + fees.regulatory + fees.slippage)


#: Stock and ETF costs: a flat commission, no per-contract regulatory fee. The option
#: model charges per **contract**, and applying it to shares was the error in the first
#: version of this file -- it made the "hand calculation" disagree with its own assertion.
STOCK_COSTS = CostConfig(
    adverse_fraction=1.0,
    commission_per_contract=0.0,
    min_commission_per_order=1.00,
    regulatory_fee_per_contract=0.0,
    slippage_per_contract=0.0,
)


class TestBuyAndHold:
    """Stage 1's criterion: buy-and-hold matches a hand calculation.

    Worked on the underlying, with no option involved, because that is what the stage is:
    a minimal loop with daily bars, one entry, one exit and a flat fee. If this does not
    match, nothing above it can be trusted either.

    The first version of this got it wrong in the way that matters most here. The
    calculation in the comment said fees of 2.00 and a net of 1,996.00; the assertion said
    1,995.90, because the code was charging an option's per-contract regulatory fee on
    shares. **The assertion had been written from the code's output rather than from the
    paper**, which is the one thing this file exists not to do -- and it passed.
    """

    #: The round trip, worked on paper. Buy 100 shares at 400.00, sell at 420.00, 1.00
    #: commission each way and no per-contract fees because these are shares.
    #:
    #:     gross = (420.00 - 400.00) x 100 = 2,000.00
    #:     fees  = 1.00 + 1.00             =     2.00
    #:     net   = 2,000.00 - 2.00         = 1,998.00
    #:
    #: A constant rather than a test. On its own it asserted its own arithmetic and
    #: called nothing -- which is the very thing this file was written to stop. As a
    #: constant it is what the test below compares the code against, which is the only
    #: job it ever had.
    SHARES, BUY, SELL, FEE_EACH = 100, 400.00, 420.00, 1.00
    GROSS, NET = 2_000.00, 1_998.00

    def test_the_paper_figures_are_internally_consistent(self):
        """One line of arithmetic, so a typo in the constants above is caught before it
        is used as the standard everything else is measured against."""
        assert (self.SELL - self.BUY) * self.SHARES == pytest.approx(self.GROSS)
        assert self.GROSS - self.FEE_EACH * 2 == pytest.approx(self.NET)

    def test_the_cost_model_agrees_on_the_same_round_trip(self):
        """The same trade through `execute`, which is what the engine calls.

        bid/ask straddling each price with a 2-cent spread and lambda = 1, so the buy
        pays the ask and the sell receives the bid:

            buy  fill = 400.01,  sell fill = 419.99
            gross = (419.99 - 400.01) x 100 = 1,998.00
            fees  = 1.00 + 1.00             =     2.00
            net   = 1,998.00 - 2.00         = 1,996.00
        """
        buy_price, buy_fees = execute(bid=399.99, ask=400.01, side=Side.BUY,
                                      quantity=1, multiplier=100, costs=STOCK_COSTS)
        sell_price, sell_fees = execute(bid=419.99, ask=420.01, side=Side.SELL,
                                        quantity=1, multiplier=100, costs=STOCK_COSTS)
        assert buy_price == pytest.approx(self.BUY + 0.01)
        assert sell_price == pytest.approx(self.SELL - 0.01)
        assert buy_fees.total == pytest.approx(self.FEE_EACH)
        assert sell_fees.total == pytest.approx(self.FEE_EACH)

        # Two cents of spread cost against the paper figures, which assumed no spread.
        gross = (sell_price - buy_price) * self.SHARES
        assert gross == pytest.approx(self.GROSS - 0.02 * self.SHARES)
        assert gross - buy_fees.total - sell_fees.total == pytest.approx(
            self.NET - 0.02 * self.SHARES)

    def test_the_engine_accounts_for_a_round_trip_to_the_hand_figure(self):
        """**The stage's criterion as stated**, as closely as this system can state it.

        Buy-and-hold *of the underlying* is not a path here: this is an options ledger and
        shares only ever arrive by assignment or exercise, so there is no "buy 100 QQQ"
        to run. The equivalent minimal-loop check is a single round trip through the whole
        chain -- fill, fees, exit, record -- with the arithmetic done from the trade log's
        own numbers rather than from anything the engine computed.

        What this catches that nothing else does: the engine could price, fill and record
        consistently and still have the **recording** step wrong -- a multiplier dropped, a
        fee counted once instead of twice, a sign flipped on a short. Every other test
        compares the engine to itself and would pass.
        """
        from backtest.api.sdk import run

        result = run(
            symbol="QQQ", start="2023-01-01", end="2023-06-30",
            entry="close < sma_20 * 0.98",
            select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                    "delta_lo": 0.25, "delta_hi": 0.45},
            exit={"hold_days": 10, "dte_floor": 7},
            engine="trade_table", sizing={"method": "fixed"}, quantity=2,
        )
        trades = result.trades
        assert len(trades), "no trades, so this proves nothing"

        multiplier = result.raw.config.contract.multiplier
        for _, t in trades.iterrows():
            #   gross = (exit price - entry price) x multiplier x quantity
            #   net   = gross - fees
            gross = (t["exit_price"] - t["entry_price"]) * multiplier * t["quantity"]
            assert t["gross_pnl"] == pytest.approx(gross, abs=1e-6), (
                f"trade {t['trade_id']}: the log says gross {t['gross_pnl']:,.4f} and "
                f"({t['exit_price']:.4f} - {t['entry_price']:.4f}) x {multiplier} x "
                f"{t['quantity']} is {gross:,.4f}"
            )
            assert t["net_pnl"] == pytest.approx(gross - t["fees"], abs=1e-6)

            #   fees = commission + regulatory + slippage, both sides
            parts = t["fee_commission"] + t["fee_regulatory"] + t["fee_slippage"]
            assert t["fees"] == pytest.approx(parts, abs=1e-6)

    def test_the_round_trip_pays_commission_twice(self):
        """One fee where there should be two is a plausible number, and it flatters."""
        from backtest.api.sdk import run

        result = run(
            symbol="QQQ", start="2023-01-01", end="2023-06-30",
            entry="close < sma_20 * 0.98",
            select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                    "delta_lo": 0.25, "delta_hi": 0.45},
            exit={"hold_days": 10, "dte_floor": 7},
            engine="trade_table", sizing={"method": "fixed"}, quantity=2,
        )
        costs = result.raw.config.costs
        # Two contracts: max(2 x 0.65, 1.00) = 1.30 per side, 2.60 for the round trip.
        one_side = max(2 * costs.commission_per_contract, costs.min_commission_per_order)
        traded = result.trades.loc[
            ~result.trades["exit_reason"].isin(["expiry_itm", "expiry_otm"])]
        assert len(traded), "every trade expired, so no round trip to check"
        assert traded["fee_commission"].iloc[0] == pytest.approx(one_side * 2)


class TestOneLongCallToExpiry:
    """Stage 3's criterion: a single long call held to expiry, including fees and
    settlement.

    The whole chain in one calculation: the fill through the spread, the per-contract
    multiplier, the order-minimum commission, and intrinsic value at expiry.
    """

    STRIKE = 400.0
    EXPIRY = date(2023, 3, 17)

    @pytest.mark.req("4.2.settle")
    def test_in_the_money_at_expiry(self):
        # Buy 2 contracts of the 400 call at bid 4.80 / ask 5.20, lambda = 1.
        #
        #   entry fill       = 5.20                    (the ask)
        #   paid             = 5.20 x 100 x 2          = 1,040.00
        #   entry fees       = max(2 x 0.65, 1.00) + 2 x 0.05
        #                    = 1.30 + 0.10             =     1.40
        #
        # The underlying settles at 412.50, so the call is 12.50 in the money.
        #
        #   intrinsic        = 412.50 - 400.00         =    12.50
        #   received         = 12.50 x 100 x 2         = 2,500.00
        #   expiry fees      = 0                       (settled, not traded)
        #
        #   gross            = 2,500.00 - 1,040.00     = 1,460.00
        #   net              = 1,460.00 - 1.40         = 1,458.60
        quantity = 2
        entry_fill = fill_price(4.80, 5.20, Side.BUY, COSTS)
        entry_fees = order_fees(quantity, COSTS, MULTIPLIER)
        paid = entry_fill * MULTIPLIER * quantity

        settle = 412.50
        intrinsic = max(settle - self.STRIKE, 0.0)
        received = intrinsic * MULTIPLIER * quantity

        assert entry_fill == pytest.approx(5.20)
        assert paid == pytest.approx(1_040.00)
        assert entry_fees.total == pytest.approx(1.40)
        assert intrinsic == pytest.approx(12.50)
        assert received == pytest.approx(2_500.00)
        assert received - paid == pytest.approx(1_460.00)
        assert received - paid - entry_fees.total == pytest.approx(1_458.60)

    def test_out_of_the_money_at_expiry_loses_the_premium_and_the_fees(self):
        # Same entry, underlying settles at 395.00: the call expires worthless.
        #
        #   intrinsic        = max(395.00 - 400.00, 0) =     0.00
        #   gross            = 0 - 1,040.00            = -1,040.00
        #   net              = -1,040.00 - 1.40        = -1,041.40
        #
        # **The maximum loss is the whole premium plus the fees**, which is the fact the
        # risk_pct sizer rests on.
        quantity = 2
        entry_fill = fill_price(4.80, 5.20, Side.BUY, COSTS)
        entry_fees = order_fees(quantity, COSTS, MULTIPLIER)
        paid = entry_fill * MULTIPLIER * quantity

        intrinsic = max(395.00 - self.STRIKE, 0.0)
        assert intrinsic == 0.0
        assert -paid - entry_fees.total == pytest.approx(-1_041.40)

    def test_a_put_settles_the_other_way(self):
        # The 400 put with the underlying at 387.25:
        #   intrinsic = 400.00 - 387.25 = 12.75
        intrinsic = max(self.STRIKE - 387.25, 0.0)
        assert intrinsic == pytest.approx(12.75)

    @pytest.mark.parametrize("right,offset", [(Right.CALL, -15.0), (Right.CALL, 15.0),
                                              (Right.PUT, -15.0), (Right.PUT, 15.0)])
    def test_the_engine_settles_a_held_option_the_same_way(self, market, chain, signals,
                                                           config, right, offset):
        """Through the engine's own settlement, against the formula worked above.

        The calculation above says what the answer is; this says the engine produces it.
        Both sides of the money for both rights, because an intrinsic computed with the
        subtraction the wrong way round is right half the time and reported without
        complaint the other half.

        A held-to-expiry trade is hard to produce from a backtest -- the days-to-expiry
        floor closes everything that can be closed -- so the engine's settlement step is
        called directly rather than hunted for in a run.
        """
        from backtest.core.types import ContractId
        from backtest.engine.vectorized import TradeTableEngine

        engine = TradeTableEngine(market, chain, signals, config)
        expiry = next(d for d in reversed(market.calendar.sessions)
                      if market.spot(d).is_present and d < config.end)
        settle = market.spot(expiry).unwrap()
        strike = round(settle + offset, 2)

        # Worked by hand, from the settlement price the market actually had:
        #   a call is worth  max(settle - strike, 0)
        #   a put  is worth  max(strike - settle, 0)
        expected = (max(settle - strike, 0.0) if right is Right.CALL
                    else max(strike - settle, 0.0))

        contract = ContractId(symbol="QQQ", expiry=expiry, strike=strike, right=right)
        quantity = 2
        entry_price = 5.20
        record = engine._settle_at_expiry(
            trade_id=1, contract=contract, entry_day=config.start,
            entry_price=entry_price,
            entry_fees=order_fees(quantity, config.costs, config.contract.multiplier),
            entry_quote={"asof": config.start, "underlying_price": settle,
                         "bid": 5.0, "ask": 5.4, "iv": 0.2},
            strategy=_held_to_expiry(signals, right, quantity), gaps=0, deferred=0,
        )
        assert record is not None
        assert record.exit_price == pytest.approx(expected)

        multiplier = config.contract.multiplier
        gross = (expected - entry_price) * multiplier * quantity
        assert record.gross_pnl == pytest.approx(gross)
        assert record.net_pnl == pytest.approx(gross - record.fees)
        assert record.exit_reason.value == ("expiry_itm" if expected > 0 else "expiry_otm")


def _held_to_expiry(signals, right, quantity):
    """A strategy whose only job is to carry the quantity and the right into settlement."""
    from backtest.engine.strategy import Strategy
    from backtest.portfolio.positioning import Sizing
    from backtest.signals.contracts import SelectionRule
    from backtest.signals.entry.expression import parse_condition
    from backtest.signals.strategy import EntryRule, ExitRule

    condition, requires = parse_condition("close > sma_20", signals.names)
    return Strategy(
        entry=EntryRule("close > sma_20", condition, requires, is_expression=True),
        select=SelectionRule(right=right, target_dte=30, dte_window=(21, 45),
                             target_delta=0.30),
        exit=ExitRule(hold_days=90, dte_floor=2),
        quantity=quantity,
        sizing=Sizing(method="fixed", contracts=quantity),
    )


class TestBlackScholesIdentity:
    """The only engine-level check in this suite that is not the code confirming itself.

    Every other test compares the engine to the engine: the two engines agree, the
    invariants hold, the baseline has not moved. All of them pass on a system that has
    been consistently wrong since the day it was written.

    The Black-Scholes partial differential equation is not. It is a mathematical identity
    that any correct set of (price, delta, gamma, theta) must satisfy:

        theta + ½ sigma² S² gamma + r S delta - r V = 0

    It is computed here, from the numbers the pricer returns, with no reference to how it
    computed them. A sign flipped on theta, a greek in the wrong units, a per-day figure
    where a per-year one belongs, a dividend yield applied the wrong way -- each breaks
    this and each would otherwise produce prices that look entirely ordinary.

    It is also what pins the **conventions**. The residual is zero only with theta per
    year; per day it is off by four orders of magnitude. The units are not documented
    anywhere a caller would see, so this is where they are fixed.
    """

    CASES = [
        # spot, strike, years, rate, vol, dividend yield
        (400.0, 410.0, 0.25, 0.045, 0.22, 0.0),     # out of the money
        (400.0, 390.0, 0.25, 0.045, 0.22, 0.0),     # in the money
        (400.0, 400.0, 0.08, 0.045, 0.22, 0.0),     # at the money, short dated
        (400.0, 400.0, 2.00, 0.045, 0.22, 0.0),     # at the money, long dated
        (400.0, 360.0, 0.50, 0.045, 0.45, 0.0),     # deep, high volatility
        (400.0, 410.0, 0.25, 0.000, 0.22, 0.0),     # zero rates
        (400.0, 410.0, 0.25, 0.045, 0.22, 0.02),    # with a dividend yield
    ]

    @pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
    @pytest.mark.parametrize("spot,strike,years,rate,vol,div", CASES)
    def test_the_pde_holds(self, right, spot, strike, years, rate, vol, div):
        import numpy as np

        from backtest.data.options.pricing import bs_price, greeks

        value = float(np.asarray(bs_price(spot, strike, years, rate, vol, div, right)))
        g = {k: float(np.asarray(v))
             for k, v in greeks(spot, strike, years, rate, vol, div, right).items()}

        # With a continuous dividend yield the carry term is (r - q) S delta, and the
        # discounting of the stock leg is what the q in d1 already accounts for.
        residual = (g["theta"]
                    + 0.5 * vol ** 2 * spot ** 2 * g["gamma"]
                    + (rate - div) * spot * g["delta"]
                    - rate * value)
        assert residual == pytest.approx(0.0, abs=1e-6), (
            f"{right.value} S={spot} K={strike} T={years}: the PDE leaves {residual:.6f}, "
            f"so the price and the greeks are not describing the same option"
        )

    def test_theta_is_per_year_not_per_day(self):
        """Pinned by the identity above, and stated here because the units appear nowhere
        a caller would see them.

        A reader who takes theta for a daily decay and divides by nothing overstates the
        decay by 365x; one who multiplies understates it by the same. The P&L attribution
        depends on getting this right and would show the error as residual.
        """
        import numpy as np

        from backtest.data.options.pricing import bs_price, greeks

        s, k, t, r, vol = 400.0, 400.0, 0.25, 0.045, 0.22
        g = {kk: float(np.asarray(v))
             for kk, v in greeks(s, k, t, r, vol, 0.0, Right.CALL).items()}
        value = float(np.asarray(bs_price(s, k, t, r, vol, 0.0, Right.CALL)))

        # One day on: the price should fall by roughly theta / 365, not by theta.
        one_day = float(np.asarray(
            bs_price(s, k, t - 1 / 365, r, vol, 0.0, Right.CALL)))
        decay = value - one_day
        assert decay == pytest.approx(-g["theta"] / 365, rel=0.02), (
            f"a day of decay is {decay:.4f} and theta/365 is {-g['theta'] / 365:.4f}"
        )

    def test_put_call_parity(self):
        """The other identity, and it catches what the PDE does not: a put priced by a
        formula of its own rather than from the call.

            C - P = S e^(-qT) - K e^(-rT)
        """
        import math

        import numpy as np

        from backtest.data.options.pricing import bs_price

        s, k, t, r, vol, q = 400.0, 410.0, 0.25, 0.045, 0.22, 0.02
        call = float(np.asarray(bs_price(s, k, t, r, vol, q, Right.CALL)))
        put = float(np.asarray(bs_price(s, k, t, r, vol, q, Right.PUT)))
        parity = s * math.exp(-q * t) - k * math.exp(-r * t)
        assert call - put == pytest.approx(parity, abs=1e-8)

    def test_the_chain_prices_what_the_formula_says(self, chain, market, config):
        """And the same again through the chain the engine actually reads.

        The pricer can be right while the chain feeds it the wrong spot, the wrong tenor
        or the wrong volatility -- in which case every number downstream is wrong and
        every engine-versus-engine test still passes.

        **Every input comes from where the chain gets it**, which is the correction here.
        This passed a dividend yield of 0.0 and a tenor of ``days / 365``, neither of
        which is what the chain uses, and it matched to 1e-6 anyway -- because this
        fixture has no ex-dividend date inside its range and because the year fraction
        happens to agree on these expiries. Both are properties of the data, not of the
        code, and a test that depends on them is a test that stops meaning anything when
        the data changes without anything saying so.
        """
        import numpy as np

        from backtest.data.options.pricing import bs_price

        day = next(d for d in market.calendar.sessions
                   if config.start <= d <= config.end and len(chain.snapshot(d)))
        snap = chain.snapshot(day)
        spot = market.spot(day).unwrap()
        rate = market.risk_free_rate(day)

        checked = 0
        for _, row in snap.head(12).iterrows():
            years = config.time.year_fraction(day, row["expiry"])
            if years <= 0 or not np.isfinite(row["iv"]):
                continue
            right = Right.CALL if row["right"] == "C" else Right.PUT
            div = market.dividend_yield(day, row["expiry"], spot)
            expected = float(np.asarray(
                bs_price(spot, float(row["strike"]), years, rate, float(row["iv"]),
                         div, right)))
            mid = (float(row["bid"]) + float(row["ask"])) / 2.0
            assert mid == pytest.approx(expected, rel=1e-6), (
                f"{right.value} {row['strike']} expiring {row['expiry']}: the chain's mid "
                f"is {mid:.4f} and Black-Scholes at its own IV says {expected:.4f}"
            )
            checked += 1
        assert checked >= 5, "too few contracts checked for this to mean anything"

    def test_the_match_above_is_sensitive_to_every_input_it_takes(self, chain, market,
                                                                  config):
        """The match is only evidence if a wrong input would break it.

        Taking each input from the right place is half the fix; the other half is knowing
        the comparison would notice if one were wrong. The dividend yield is the one that
        matters most here -- this fixture's is zero over most of its range, so a test
        that passed q = 0 by hand agreed with the chain for the wrong reason, and would
        have gone on agreeing if the pricer had ignored q entirely.

        So each input is perturbed by a realistic amount and the match has to fail.
        """
        import numpy as np

        from backtest.data.options.pricing import bs_price

        day = next(d for d in market.calendar.sessions
                   if config.start <= d <= config.end and len(chain.snapshot(d)))
        row = next(r for _, r in chain.snapshot(day).iterrows()
                   if np.isfinite(r["iv"])
                   and config.time.year_fraction(day, r["expiry"]) > 0.05)
        spot = market.spot(day).unwrap()
        right = Right.CALL if row["right"] == "C" else Right.PUT
        base = dict(s=spot, k=float(row["strike"]),
                    t=config.time.year_fraction(day, row["expiry"]),
                    r=market.risk_free_rate(day), vol=float(row["iv"]),
                    q=market.dividend_yield(day, row["expiry"], spot))

        def price(**over):
            a = base | over
            return float(np.asarray(bs_price(a["s"], a["k"], a["t"], a["r"], a["vol"],
                                             a["q"], right)))

        mid = (float(row["bid"]) + float(row["ask"])) / 2.0
        assert price() == pytest.approx(mid, rel=1e-6)

        # Each of these is the size of a plausible mistake, not a token nudge: a 1%
        # dividend yield, a rate off by a quarter point, a tenor off by a week, a spot
        # off by a dollar, an IV off by a point.
        for what, over in (("the dividend yield", {"q": base["q"] + 0.01}),
                           ("the rate", {"r": base["r"] + 0.0025}),
                           ("the tenor", {"t": base["t"] + 7 / 365}),
                           ("the spot", {"s": base["s"] + 1.0}),
                           ("the volatility", {"vol": base["vol"] + 0.01})):
            assert price(**over) != pytest.approx(mid, rel=1e-6), (
                f"{what} was changed and the price still matched the chain to 1e-6, so "
                f"the comparison above is not actually testing it"
            )
