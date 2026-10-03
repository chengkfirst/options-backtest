"""Contract selection: turning "a call about 30 days out, delta about 0.30" into
one specific contract.

Two conventions live here and must not be confused:

- The **target days to expiry** is in **calendar days** (30 DTE means 30 calendar
  days, as the market means it).
- The **executability floor** is in **trading days**, because it answers to the
  execution lag — an order placed on T is executed on T+1, and the contract has to
  still be tradable then.

Every selection returns its whole funnel: which contract was chosen, how many were
eliminated at each step, and why it failed. Without that, "why was nothing opened
that day" can only be guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from backtest.core.config import BacktestConfig
from backtest.core.clock import TradingCalendar
from backtest.data.options.chain import relative_spread
from backtest.core.errors import StartupError
from backtest.core.maybe import Maybe
from backtest.core.types import ContractId, Right, require


@dataclass(frozen=True, slots=True)
class SelectionRule:
    """A selection rule. Exactly one of ``target_delta`` and ``moneyness``.

    **Nothing here is defaulted.** This is slot 2b, and delta and tenor are the two most
    consequential parameters an option buyer sets -- leverage, convexity and theta decay
    all move with them. A platform-side ``target_delta = 0.30`` would decide the single
    most researched choice in the strategy and then never appear in the configuration a
    person reads back.

    Unlike an exit level there is no safe "absent" state: a chain holds hundreds of
    contracts and selection has to narrow it. So these are **required** rather than
    optional, and omitting them raises at construction.
    """

    right: Right
    target_dte: int
    dte_window: tuple[int, int]
    target_delta: float | None = None
    #: Moneyness relative to spot; positive is out of the money (for a call, a
    #: higher strike).
    moneyness: float | None = None
    min_volume: float | None = None
    min_open_interest: float | None = None
    max_relative_spread: float | None = None

    # ── the feasible region: hard constraints, nothing outside is considered ──
    #
    # The professional form is not "pick a point" but "mark out a feasible region,
    # then order within it by an objective". Pinning delta = 0.30 has two problems:
    # the chain may not hold that contract (so something far away gets picked), and
    # **a conclusion that holds only at that one point is noise** — live parameters
    # never land exactly there.
    #: Acceptable band for delta, in absolute value. Given this, ``target_delta``
    #: becomes merely the objective used to order within the band.
    delta_band: tuple[float, float] | None = None
    #: Acceptable band for the contract's own IV. A buyer wants it low, a seller
    #: wants it high.
    iv_band: tuple[float, float] | None = None


    def __post_init__(self) -> None:
        lo, hi = self.dte_window
        require(0 < lo <= hi, f"invalid dte_window: {self.dte_window}")
        require(lo <= self.target_dte <= hi,
                f"target_dte {self.target_dte} is outside {self.dte_window}")
        require(
            (self.target_delta is None) != (self.moneyness is None),
            "exactly one of target_delta and moneyness must be given",
        )
        if self.target_delta is not None:
            require(0 < abs(self.target_delta) < 1,
                    f"target_delta must lie between 0 and 1: {self.target_delta}")
        for name, band in (("delta_band", self.delta_band), ("iv_band", self.iv_band)):
            if band is not None:
                lo, hi = band
                require(lo < hi, f"{name}'s lower bound must be below its upper: {band}")


#: The columns a selection function gets on top of ``CHAIN_COLUMNS``. All
#: precomputed — derive the same thing by hand every time and sooner or later a
#: sign comes out backwards.
DERIVED_COLUMNS: tuple[str, ...] = (
    "dte", "sessions_left", "mid", "spread", "moneyness", "is_call", "is_put",
)

#: A custom selector: takes the day's chain and the **decision day's** view, and returns
#: the chosen row (or None for no trade).
#:
#: The second argument is a :class:`~backtest.core.dataview.DataView` when the engine has
#: one — cut at the decision day, so a read of the fill day **raises**. It falls back to a
#: bare signal view only where no engine built one. Protecting the plan hook and not this
#: one would be worse than protecting neither: the protection would read as systemic while
#: leaving the gap a custom selector is most likely to walk into.
#:
#: Why the chain is the execution day's while the view is the decision day's — see
#: ``enrich_chain``.
ContractChooser = Callable[[pd.DataFrame, object], object]


def enrich_chain(
    chain: pd.DataFrame,
    config: BacktestConfig,
    calendar: TradingCalendar,
    asof: date,
) -> pd.DataFrame:
    """Put the day's chain into a shape you can pick from directly on greeks and iv.

    Two things happen:

    1. **Derived columns are added.** ``dte`` in calendar days (30 DTE means 30
       calendar days, as the market means it), ``sessions_left`` in trading days
       (executability uses this convention; the two must not be mixed); ``mid`` and
       ``spread`` (the relative bid/ask spread); and ``moneyness``, where
       **positive always means out of the money** for calls and puts alike, so the
       direction never has to be reasoned out again.

    2. **Contracts that cannot be executed are removed first.** The decision is on
       T, the fill on T+1, and only what is still tradable then survives. Removing
       them here rather than validating afterwards is what makes "pick freely on
       greeks and iv" **safe by construction**: you cannot pick something
       unopenable, so there is nothing to remember to check.

    **On look-ahead**: this chain is the **execution day's**, while the signal view
    passed to the selector is the **decision day's**. That is not a slip. The real
    process is that at the close of T you decide, from what you can see then, what
    kind of contract to buy; the order fills at the close of T+1 and which contract
    it is gets resolved at that moment. So the rule may use T's information and the
    resolution may use T+1's chain — but the rule itself must not see T+1's market,
    or it is picking a strike out of the fill day's move.
    """
    if chain.empty:
        return chain

    out = chain.copy()
    expiry = out["expiry"].map(_as_date)
    out["dte"] = [(e - asof).days for e in expiry]
    out["sessions_left"] = [calendar.sessions_between(asof, e) for e in expiry]
    out["mid"] = (out["bid"] + out["ask"]) / 2.0
    out["spread"] = relative_spread(out["bid"], out["ask"])

    spot = out["underlying_price"].astype(float)
    # The ``right`` column holds "C" / "P" (matching the contract id). But
    # `chain.right == "call"` is the natural instinct, and that silently matches
    # zero rows. Hence the boolean columns, so nobody has to remember.
    is_call = out["right"] == Right.CALL.value
    out["is_call"] = is_call
    out["is_put"] = ~is_call
    # For a call a higher strike is further out of the money; for a put a lower
    # one is. Unified so that positive always means out of the money.
    out["moneyness"] = np.where(is_call, out["strike"] / spot - 1.0, 1.0 - out["strike"] / spot)

    return out.loc[out["sessions_left"] >= config.min_days_to_expiry].reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """The contract chosen, together with the whole funnel."""

    contract: Maybe[ContractId]
    quote: dict | None
    trace: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    skipped_filters: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.contract.is_present

    def describe(self) -> str:
        steps = " → ".join(f"{name}:{n}" for name, n in self.trace)
        if self.ok:
            return f"chose {self.contract.unwrap()} | {steps}"
        return f"nothing chosen ({self.contract.reason}) | {steps}"


def select_contract(
    chain: pd.DataFrame,
    rule: "SelectionRule | ContractChooser",
    config: BacktestConfig,
    calendar: TradingCalendar,
    asof: date,
    view: object = None,
) -> SelectionResult:
    """Choose one contract from a day's chain.

    ``rule`` comes in two forms:

    - ``SelectionRule`` — declarative, "about 30 days, delta about 0.30". Fast and
      sweepable, but cannot express "adjust to what the market is doing".
    - **a function** — ``f(chain, view)``, which filters the whole chain itself and
      returns one row or ``None``. The chain already carries the derived columns
      and has the unexecutable contracts removed; see ``enrich_chain``.
    """
    if callable(rule):
        return _choose_by_function(chain, rule, config, calendar, asof, view)
    trace: list[tuple[str, int]] = [("on the chain", len(chain))]
    skipped: list[str] = []

    if chain.empty:
        return SelectionResult(Maybe.missing("the chain is empty that day"), None,
                               tuple(trace))

    pool = chain.loc[chain["right"] == rule.right.value]
    trace.append(("by option type", len(pool)))
    if pool.empty:
        return SelectionResult(Maybe.missing("the chain holds no contract of that type"),
                               None, tuple(trace))

    pool = _filter_expiry(pool, rule, config, calendar, asof, trace)
    if pool is None or pool.empty:
        return SelectionResult(
            Maybe.missing("no expiry both inside the window and executable"), None,
            tuple(trace)
        )

    pool = _filter_liquidity(pool, rule, config, trace, skipped)
    if not pool.empty:
        pool = _apply_bands(pool, rule, trace, skipped)
    if pool.empty:
        return SelectionResult(
            Maybe.missing("no candidate survived the liquidity filters"), None,
            tuple(trace), tuple(skipped)
        )

    row = _pick_strike(pool, rule, trace)
    if row is None:
        return SelectionResult(
            Maybe.missing("cannot settle on a strike (delta is unavailable)"), None,
            tuple(trace), tuple(skipped)
        )

    contract = ContractId(
        symbol=str(row["symbol"]),
        expiry=_as_date(row["expiry"]),
        right=Right(row["right"]),
        strike=float(row["strike"]),
    )
    return SelectionResult(Maybe.some(contract), row.to_dict(), tuple(trace), tuple(skipped))


def _choose_by_function(
    chain: pd.DataFrame,
    chooser: "ContractChooser",
    config: BacktestConfig,
    calendar: TradingCalendar,
    asof: date,
    view: object,
) -> SelectionResult:
    """Run the user's selection function and validate its return into a contract
    that really exists and really can be opened.

    The validation is not optional. A user function can return anything — an empty
    DataFrame, a row it assembled itself, a strike that is not on the chain. Left
    unchecked, the backtest carries a non-existent contract through to the end and
    produces a wholly believable false result.
    """
    trace: list[tuple[str, int]] = [("on the chain", len(chain))]
    pool = enrich_chain(chain, config, calendar, asof)
    trace.append(("executable (still tradable on T+1)", len(pool)))
    if pool.empty:
        return SelectionResult(Maybe.missing("no executable contract that day"), None,
                               tuple(trace))

    try:
        picked = chooser(pool, view)
    except Exception as exc:  # noqa: BLE001
        # Never swallowed. An error inside the user's function has to be raised
        # with the day and the cause, or there is nowhere to start debugging.
        raise StartupError(
            f"the selection function raised on {asof}: {type(exc).__name__}: {exc}"
        ) from exc

    if picked is None:
        # None can mean "genuinely no trade today" or a filter written wrong that
        # returns nothing every day. Those are indistinguishable from the result,
        # so what **was actually** selectable that day is reported alongside — a
        # wrong `right` value, or delta absent throughout, is then obvious from
        # one event.
        return SelectionResult(
            Maybe.missing(f"the selection function returned None (no trade today) | "
                          f"available that day: {_profile(pool)}"),
            None, tuple(trace),
        )

    row = _as_single_row(picked, asof)
    if row is None:
        return SelectionResult(Maybe.missing("the selection function returned nothing"),
                               None, tuple(trace))

    key = (str(row["symbol"]), _as_date(row["expiry"]), str(row["right"]), float(row["strike"]))
    match = pool.loc[
        (pool["symbol"].astype(str) == key[0])
        & (pool["expiry"].map(_as_date) == key[1])
        & (pool["right"].astype(str) == key[2])
        & (pool["strike"].astype(float) == key[3])
    ]
    if match.empty:
        raise StartupError(
            f"the contract the selection function returned on {asof} is not on that "
            f"day's executable chain: {key}. Return a row from the chain that was "
            f"passed in; do not assemble one."
        )

    row = match.iloc[0]
    trace.append(("chosen by the function", 1))
    contract = ContractId(
        symbol=str(row["symbol"]),
        expiry=_as_date(row["expiry"]),
        right=Right(row["right"]),
        strike=float(row["strike"]),
    )
    return SelectionResult(Maybe.some(contract), row.to_dict(), tuple(trace))


def _profile(pool: pd.DataFrame) -> str:
    """A one-line profile of what was selectable that day, so that "nothing was
    selected" explains itself."""
    def rng(col: str, fmt: str = "{:.2f}") -> str:
        v = pool[col].dropna()
        if v.empty:
            return f"{col}=absent throughout"
        return f"{col} {fmt.format(v.min())}–{fmt.format(v.max())}"

    rights = "/".join(sorted(pool["right"].astype(str).unique()))
    return (f"{len(pool)} contracts, right values {rights}, "
            f"{rng('dte', '{:.0f}')}, {rng('delta')}, {rng('iv')}, {rng('spread')}")


def _as_single_row(picked, asof: date):
    """Reduce the selection function's return to one row. Accepts a Series, a
    single-row DataFrame, and an empty result."""
    if isinstance(picked, pd.DataFrame):
        if picked.empty:
            return None
        if len(picked) > 1:
            raise StartupError(
                f"the selection function returned {len(picked)} rows on {asof}. One "
                f"contract at a time — multi-leg needs multi-leg support; for now "
                f"return a single row."
            )
        return picked.iloc[0]
    if isinstance(picked, pd.Series):
        return picked
    raise StartupError(
        f"the selection function returned {type(picked).__name__} on {asof}; it must "
        f"return a row of the chain (a Series) or None."
    )


def _as_date(value) -> date:
    return value if isinstance(value, date) else pd.Timestamp(value).date()


def _filter_expiry(
    pool: pd.DataFrame,
    rule: SelectionRule,
    config: BacktestConfig,
    calendar: TradingCalendar,
    asof: date,
    trace: list[tuple[str, int]],
) -> pd.DataFrame | None:
    expiries = sorted({_as_date(v) for v in pool["expiry"].unique()})
    lo, hi = rule.dte_window

    in_window = [e for e in expiries if lo <= (e - asof).days <= hi]
    trace.append(("expiry inside the window", len(in_window)))
    if not in_window:
        return None

    # The executability floor: a contract expiring before or on the execution day
    # is unusable. The floor is in trading days, and the config layer has already
    # guaranteed it is at least the execution lag plus one.
    executable = [
        e for e in in_window if calendar.sessions_between(asof, e) >= config.min_days_to_expiry
    ]
    trace.append(("past the executability floor", len(executable)))
    if not executable:
        return None

    # Closest to the target; on a tie take the nearer one (a deterministic rule).
    chosen = min(executable, key=lambda e: (abs((e - asof).days - rule.target_dte), e))
    subset = pool.loc[pool["expiry"].map(_as_date) == chosen]
    trace.append(("after settling the expiry", len(subset)))
    return subset


def _filter_liquidity(
    pool: pd.DataFrame,
    rule: SelectionRule,
    config: BacktestConfig,
    trace: list[tuple[str, int]],
    skipped: list[str],
) -> pd.DataFrame:
    """Liquidity filters. When a field is unavailable the constraint is **skipped
    and flagged**; an absent field never counts as a failure."""
    checks = (
        ("volume", rule.min_volume, "minimum volume"),
        ("open_interest", rule.min_open_interest, "minimum open interest"),
    )
    for column, threshold, label in checks:
        if threshold is None:
            continue
        if pool[column].isna().all():
            skipped.append(f"{label} (the data source does not supply {column})")
            continue
        pool = pool.loc[pool[column] >= threshold]
        trace.append((label, len(pool)))
        if pool.empty:
            return pool

    cap = rule.max_relative_spread or config.costs.max_relative_spread
    spread = relative_spread(pool["bid"], pool["ask"])
    pool = pool.loc[spread.notna() & (spread <= cap)]
    trace.append(("spread cap", len(pool)))
    return pool


def _apply_bands(pool: pd.DataFrame, rule: SelectionRule, trace, skipped):
    """Feasible-region filters. When a field is unavailable the constraint is
    **skipped and flagged**; an absent field never counts as a failure."""
    if rule.delta_band is not None:
        lo, hi = rule.delta_band
        if pool["delta"].isna().all():
            skipped.append("delta band (the data source does not supply delta)")
        else:
            pool = pool.loc[pool["delta"].abs().between(lo, hi)]
            trace.append((f"delta∈[{lo},{hi}]", len(pool)))
    if rule.iv_band is not None:
        lo, hi = rule.iv_band
        if pool["iv"].isna().all():
            skipped.append("IV band (the data source does not supply iv)")
        else:
            pool = pool.loc[pool["iv"].between(lo, hi)]
            trace.append((f"IV∈[{lo},{hi}]", len(pool)))
    return pool


def _pick_strike(pool: pd.DataFrame, rule: SelectionRule, trace: list[tuple[str, int]]):
    if rule.target_delta is not None:
        usable = pool.loc[pool["delta"].notna()]
        trace.append(("delta available", len(usable)))
        if usable.empty:
            return None
        target = abs(rule.target_delta)
        distance = (usable["delta"].abs() - target).abs()
        # On a tie take the one further out of the money: a higher strike for a
        # call, a lower one for a put.
        tie_break = -usable["strike"] if rule.right is Right.CALL else usable["strike"]
        order = np.lexsort((tie_break.to_numpy(), distance.to_numpy()))
        return usable.iloc[order[0]]

    spot = float(pool["underlying_price"].iloc[0])
    sign = 1.0 if rule.right is Right.CALL else -1.0
    target_strike = spot * (1 + sign * rule.moneyness)
    distance = (pool["strike"] - target_strike).abs()
    order = np.lexsort((pool["strike"].to_numpy(), distance.to_numpy()))
    trace.append(("located by moneyness", len(pool)))
    return pool.iloc[order[0]]
