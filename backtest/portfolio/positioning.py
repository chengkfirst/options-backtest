"""Slot 6 · Positioning — how much to buy.

## Why this is Layer 3 and not Layer 2

Sizing cannot be decided from the signal alone. It needs book state: current equity, the
premium already at risk, the book's greeks, and the liquidity cap on how much can
actually be bought. Those belong to the portfolio layer, so the sizer does too — even
though the decision feels like part of the same thought as the entry.

## The coupling that matters most

    position size = risk budget / stop distance

Fix two of the three and the third is fixed. ``risk_pct`` divides by the stop distance
for exactly this reason: with the stop at -50%, a trade's real risk is half the premium,
and treating it as a total loss halves the position. Set the stop and the size
independently and the computed position no longer matches the risk you think you took.

:func:`loss_fraction` is that divisor, and :func:`contracts_for` is where the
division happens. The engine fetches the premium and the volatility and calls them; it no
longer holds the rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from backtest.core.errors import StartupError
from backtest.core.types import require


#: Which parameter each sizing method needs, in one place.
#:
#: Read by the validation below and by ``tools/check_wiring.py``, which has to build a
#: valid instance of every method the interface offers. Two copies of this mapping would
#: drift, and the drift would show up as a method the interface can select and the backend
#: refuses -- a feature that fails only when someone picks that one option.
REQUIRES: dict[str, tuple[str, ...]] = {
    "fixed": ("contracts",),
    "risk_pct": ("risk_pct",),
    "fixed_notional": ("notional",),
    # **Two, not one.** It scales the risk-based budget by the volatility ratio, so it
    # needs the budget's parameter as well as its own. Listed as one, the validation
    # accepted a spec without `risk_pct` and the arithmetic then multiplied by None --
    # which is how two of the four methods came to be offered and never run.
    "scale_by_vol": ("target_vol", "risk_pct"),
}


@dataclass(frozen=True, slots=True)
class Sizing:
    """How position size is determined.

    For an option buyer the **maximum loss is the whole premium paid**, which makes
    "risk per trade" a clean concept: risk amount / premium per contract = contracts.
    It does not hold for a seller (whose maximum loss far exceeds the premium
    received), so selling is refused — that needs a margin model.

    ``method`` takes:

    ``"fixed"``          a fixed number of contracts. The plainest, but as equity
                         moves each trade's fraction of risk drifts.
    ``"risk_pct"``       a fixed fraction of equity at risk per trade. **The
                         default for a buyer.**
    ``"fixed_notional"`` a fixed amount committed per trade.
    ``"scale_by_vol"``   scaled inversely with the underlying's volatility: buy less
                         when it is more volatile. **Not a portfolio volatility
                         target.**

    There is no Kelly: the parameters it needs (win rate, payoff ratio) are mostly
    estimation noise over a few hundred option trades, and it sets the bet size
    directly from them; the binary formula also does not apply to options with a stop
    (a loss is not a total loss). Keeping a method that cannot be estimated only
    invites its use.
    """

    #: ``contracts`` is the one parameter with a defensible default: a fixed count of one
    #: is the plainest possible choice and is visible in every trade of the log. The other
    #: three are **not** defaulted -- "2% of equity per trade" is slot 6, a strategy choice
    #: the architecture document permits optimising under its protocol, and a platform-side
    #: 0.02 would size every position in the book without ever appearing in the
    #: configuration a person reads back. Each is required when its method is chosen.
    method: str = "fixed"
    contracts: int = 1                 # fixed
    risk_pct: float | None = None      # risk_pct: the fraction of equity at risk per trade
    notional: float | None = None      # fixed_notional
    target_vol: float | None = None    # scale_by_vol: the reference annual volatility
    max_contracts: int = 50            # no method may exceed this

    def __post_init__(self) -> None:
        require(self.method in REQUIRES,
                f"unknown sizing method {self.method!r}; one of {sorted(REQUIRES)}")
        require(self.contracts >= 1, "contracts must be at least 1")
        # Each method's own parameter is required when that method is chosen, and ignored
        # otherwise. Required rather than defaulted: it decides the size of every position
        # in the book, and a value the platform supplied does not appear in the
        # configuration a person reads back when asking what this run actually did.
        absent = [name for name in REQUIRES.get(self.method, ())
                  if getattr(self, name) is None]
        if absent:
            raise StartupError(
                f"sizing method {self.method!r} needs {', '.join(absent)}. "
                f"{'They are' if len(absent) > 1 else 'It is'} a strategy choice and the "
                f"platform does not pick it -- it would size every position in the book "
                f"and never show up in the configuration you read back"
            )
        if self.risk_pct is not None:
            require(0 < self.risk_pct <= 1, "risk_pct must lie between 0 and 1")
        if self.notional is not None:
            require(self.notional > 0, "notional must be positive")
        if self.target_vol is not None:
            require(self.target_vol > 0, "target_vol must be positive")
        require(self.max_contracts >= 1, "max_contracts must be at least 1")


def loss_fraction(stop_loss: float | None) -> float:
    """What fraction of the premium a trade loses in the worst case.

    A stop of 0.5 means out at -50%, so the real risk is half the premium. Only with no
    stop is it the whole premium (a buyer's maximum loss).

    **This is where "size = risk budget / stop distance" lands in the code.** Set the
    stop and the size independently and the computed position no longer matches the risk
    you think you took.
    """
    if stop_loss is None or not 0 < stop_loss <= 1:
        return 1.0
    return float(stop_loss)


def contracts_for(
    sizing: Sizing,
    *,
    equity: float,
    premium: float,
    stop_loss: float | None,
    volatility: float | None = None,
) -> int:
    """How many contracts the method asks for. Pure arithmetic.

    It takes numbers rather than the engine's objects on purpose: the sizer can then be
    tested on its own, instead of only through a whole backtest. Fetching the premium and
    the volatility is the engine's job, because that is data access; dividing the budget
    is this layer's.

    ``premium`` is the cost of one contract, multiplier included. ``volatility`` is
    required only by ``scale_by_vol``; passing ``None`` there returns 0 rather than
    inventing a figure.

    Zero contracts means no trade. **It is never rounded up to 1**, which would quietly
    turn "2% at risk" into "8% at risk".
    """
    if sizing.method == "fixed":
        return min(sizing.contracts, sizing.max_contracts)
    if premium <= 0:
        return 0

    def budget_at_risk() -> float:
        """Equity at risk per trade, given the stop.

        **Computed inside the branches that need it, not above them**, and that was a
        real bug rather than a tidiness point: it ran unconditionally, and `risk_pct` is
        None for `fixed_notional`, so two of the four advertised methods raised
        `TypeError: unsupported operand type(s) for *: 'float' and 'NoneType'` the moment
        anyone selected them. The methods were offered by the interface, listed in
        `REQUIRES`, and had never run.

        The absent parameter is now named rather than multiplied -- "a feature that fails
        only when someone picks that one option" is the failure `REQUIRES` exists to
        prevent, and it had it.
        """
        if sizing.risk_pct is None:
            raise StartupError(
                f"the {sizing.method} sizing method needs risk_pct, and none was given. "
                f"It sizes against the equity a trade may lose, which is risk_pct of "
                f"the account divided by what the stop gives up"
            )
        return equity * sizing.risk_pct / loss_fraction(stop_loss)

    if sizing.method == "risk_pct":
        budget = budget_at_risk()
    elif sizing.method == "fixed_notional":
        if sizing.notional is None:
            raise StartupError(
                "the fixed_notional sizing method needs notional -- the cash to commit "
                "per trade"
            )
        budget = sizing.notional
    elif sizing.method == "scale_by_vol":
        if sizing.target_vol is None:
            raise StartupError(
                "the scale_by_vol sizing method needs target_vol -- the volatility the "
                "size is scaled against"
            )
        if volatility is None:
            return 0
        # Buy less when the underlying is more volatile. **This is not a portfolio
        # volatility target** — that would need a forecast of portfolio volatility, which
        # for options has to come from the greeks. The name says what it actually does.
        budget = budget_at_risk() * (sizing.target_vol / max(volatility, 1e-6))
    else:
        raise StartupError(f"unimplemented sizing method {sizing.method}")

    return min(int(budget // premium), sizing.max_contracts)


# ══════════════════════════════════════════════════════════════
#  the four methods, as registered variants
# ══════════════════════════════════════════════════════════════
#
# **The slot protocol did not reach sizing.** The four methods existed and were
# selectable through ``sizing={"method": ...}``, and none of them was a registered
# variant -- so slot 6 read as "0 forms" and the tools built for slots did not apply to
# it: no variant swap, no slot sweep, no ablation, nothing in the composer that offers a
# slot's forms with their parameters.
#
# Registering them changes no arithmetic. Each variant is a thin call into
# :func:`contracts_for`, which stays the one implementation -- two copies of a sizing
# rule would be a drift nobody notices until the two disagree on one trade.

def _size_with(method: str, ctx: Mapping[str, Any], **params: Any) -> int:
    """One variant's body: build the ``Sizing`` it describes and ask for the count.

    ``ctx`` is the sizing context the engine supplies: equity, the premium of one
    contract, the stop, and the volatility where the method needs it. A missing input is
    **0 contracts, not a guess** -- the same rule ``contracts_for`` already applies.
    """
    spec = Sizing(method=method, **params)
    premium = ctx.get("premium")
    equity = ctx.get("equity")
    if method != "fixed" and (premium is None or equity is None):
        return 0
    return contracts_for(
        spec,
        equity=float(equity or 0.0),
        premium=float(premium or 0.0),
        stop_loss=ctx.get("stop_loss"),
        volatility=ctx.get("volatility"),
    )


def _register_sizing_variants() -> None:
    """Register one variant per method, with that method's own parameter.

    Called at import. The parameter each one needs is read from :data:`REQUIRES`, which
    is already the single place that mapping lives -- so a method added there and not
    here fails the wiring check rather than appearing as a form with no parameters.
    """
    from backtest.core.component import Param, Variant, register

    described = {
        "fixed": ("a fixed number of contracts every time. The only method that ignores "
                  "equity and premium, so it is the one to use when the question is "
                  "about the rule rather than about compounding",
                  (Param("contracts", "int", "how many contracts", lo=1, hi=1000),)),
        "risk_pct": ("risk a fixed share of equity per trade, sized so that the stop "
                     "loses that much. Needs a stop: without one the whole premium is "
                     "at risk and the share is the position",
                     (Param("risk_pct", "float", "share of equity at risk per trade",
                            lo=0.0, hi=1.0),)),
        "fixed_notional": ("spend a fixed cash amount per trade, whatever the equity "
                           "has done. Position count falls as premiums rise",
                           (Param("notional", "float", "cash spent per trade",
                                  lo=0.0, hi=1e9),)),
        "scale_by_vol": ("buy less when the underlying is more volatile. **Not a "
                         "portfolio volatility target** -- that needs a forecast of "
                         "portfolio volatility, which for options comes from the "
                         "greeks. The name says what it does",
                         (Param("target_vol", "float", "the volatility the size is "
                                "scaled against", lo=0.0, hi=5.0),
                          Param("risk_pct", "float", "share of equity at risk per "
                                "trade, which the volatility ratio then scales",
                                lo=0.0, hi=1.0))),
    }
    missing = sorted(set(REQUIRES) - set(described))
    if missing:
        raise StartupError(
            f"these sizing methods have no registered variant: {missing}. REQUIRES is "
            f"the one list of methods, and a method that is selectable and has no form "
            f"is a slot the research tools cannot reach"
        )
    for method, (describes, params) in described.items():
        declared = {p.name for p in params}
        needed = set(REQUIRES[method])
        if declared != needed:
            raise StartupError(
                f"the {method} form declares {sorted(declared)} and the method needs "
                f"{sorted(needed)}. A form offering fewer parameters than its method "
                f"requires is a form that raises the first time anyone picks it"
            )
        register(Variant(
            slot="position", name=method, version=1,
            describes=describes,
            params=params,
            decide=(lambda ctx, _m=method, **kw: _size_with(_m, ctx, **kw)),
        ))


_register_sizing_variants()
