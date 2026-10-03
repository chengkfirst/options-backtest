"""The core types that cross module boundaries. All immutable.

``TradeRecord`` is this project's most important contract: both the trade-table
engine and the event-driven engine emit this one record, which is what lets the
research and report layers work against either path, and lets the two engines'
results be compared row by row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from backtest.core.errors import StartupError
from backtest.core.invariants import trade_record_balances
from backtest.core.maybe import Maybe


class Right(str, Enum):
    CALL = "C"
    PUT = "P"


class ExerciseStyle(str, Enum):
    AMERICAN = "american"
    EUROPEAN = "european"


class SettlementStyle(str, Enum):
    PHYSICAL = "physical"
    CASH = "cash"


class QuoteSource(str, Enum):
    SYNTHETIC = "synthetic"
    VENDOR = "vendor"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class ExitReason(str, Enum):
    """The one set of exit reasons; both engines use these values and this order."""

    RISK_FORCED = "risk_forced"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    DTE_FLOOR = "dte_floor"
    TIME_EXIT = "time_exit"
    EXPIRY_ITM = "expiry_itm"
    EXPIRY_OTM = "expiry_otm"


#: Deterministic precedence when several conditions hold on the same day;
#: earlier wins.
#:
#: Stop-loss ahead of take-profit is a deliberately conservative choice: daily
#: bars do not show which came first intraday, so when both are touched on one
#: day we assume the losing side came first. That cannot flatter the strategy.
EXIT_PRIORITY: tuple[ExitReason, ...] = (
    ExitReason.RISK_FORCED,
    ExitReason.STOP_LOSS,
    ExitReason.TAKE_PROFIT,
    ExitReason.DTE_FLOOR,
    ExitReason.TIME_EXIT,
    ExitReason.EXPIRY_ITM,
    ExitReason.EXPIRY_OTM,
)

_EXIT_RANK: Mapping[ExitReason, int] = MappingProxyType(
    {reason: i for i, reason in enumerate(EXIT_PRIORITY)}
)


def resolve_exit_reason(reasons: "list[ExitReason] | tuple[ExitReason, ...]") -> ExitReason:
    """Pick one reason when several hold on the same day, by fixed precedence."""
    if not reasons:
        raise ValueError("at least one exit reason is required")
    return min(reasons, key=lambda r: _EXIT_RANK[r])


@dataclass(frozen=True, slots=True, order=True)
class ContractId:
    """A readable, deterministic contract id, e.g. ``QQQ_20240315_C_00400000``.

    Readable so a trade row can be checked by eye; deterministic so runs
    reproduce; free of any external symbol table so it works offline.
    """

    symbol: str
    expiry: date
    right: Right
    strike: float

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.isupper():
            raise ValueError(f"symbol must be non-empty upper case: {self.symbol!r}")
        if self.strike <= 0:
            raise ValueError(f"strike must be positive: {self.strike}")
        # The strike is encoded at fixed width in thousandths; finer precision
        # than 1/1000 cannot be represented.
        if abs(round(self.strike * 1000) - self.strike * 1000) > 1e-6:
            raise ValueError(f"strike is finer than a thousandth: {self.strike}")

    def __str__(self) -> str:
        return (
            f"{self.symbol}_{self.expiry:%Y%m%d}_{self.right.value}"
            f"_{round(self.strike * 1000):08d}"
        )

    @classmethod
    def parse(cls, text: str) -> "ContractId":
        try:
            symbol, expiry, right, strike = text.split("_")
            return cls(
                symbol=symbol,
                expiry=date(int(expiry[:4]), int(expiry[4:6]), int(expiry[6:8])),
                right=Right(right),
                strike=round(int(strike) / 1000, 3),
            )
        except (ValueError, KeyError) as exc:
            raise ValueError(f"cannot parse contract id: {text!r}") from exc


@dataclass(frozen=True, slots=True)
class ContractSpec:
    """Contract specification. Settlement comes from here, not from one
    system-wide assumption.

    QQQ / SPY options are American and physically settled: held to expiry in the
    money they deliver 100 shares, which has to be paid for. Index options (SPX)
    are European and cash settled. This is configuration rather than a default
    because modelling a physically settled underlying as cash settled flatters
    performance systematically.
    """

    symbol: str
    multiplier: int = 100
    exercise: ExerciseStyle = ExerciseStyle.AMERICAN
    settlement: SettlementStyle = SettlementStyle.PHYSICAL
    strike_step: float = 1.0
    vol_proxy: str = "^VXN"

    def __post_init__(self) -> None:
        if self.multiplier <= 0:
            raise ValueError("the contract multiplier must be positive")
        if self.strike_step <= 0:
            raise ValueError("the strike step must be positive")


#: The columns of the long-form option chain. Defined after what commercial
#: vendors generally supply, not trimmed to what the synthetic chain can produce.
#: A field one implementation cannot fill is marked missing; it MUST NOT be
#: dropped from the interface.
CHAIN_COLUMNS: tuple[str, ...] = (
    "asof",
    #: **When this row could first have been known**, as opposed to the day it describes.
    #:
    #: The architecture document's first data rule is that nothing enters a backtest
    #: without one. The two dates coincide for a clean daily snapshot, which is why
    #: nothing has broken while the column did not exist -- and they stop coinciding the
    #: moment a vendor delivers late, revises a figure, or backfills a gap. A row that
    #: arrived two days after the session it describes must not be visible on that
    #: session, and without this column there is nowhere to say so.
    "available_at",
    "symbol",
    "expiry",
    "strike",
    "right",
    "bid",
    "ask",
    "last",
    "volume",
    "open_interest",
    "iv",
    "delta",
    "gamma",
    "vega",
    "theta",
    "underlying_price",
    "source",
    "quality_flags",
    "greeks_model_derived",
)

#: Primary key.
CHAIN_KEY: tuple[str, ...] = ("asof", "symbol", "expiry", "strike", "right")


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """One logical trade: from the first fill to being fully closed.

    The unit of statistics is the logical trade, not the individual position —
    counted by position, something rolled three times becomes four trades, and
    both win rate and holding period are distorted.
    """

    trade_id: int
    engine: str
    symbol: str
    entry_date: date
    exit_date: date
    contract: ContractId
    quantity: int
    entry_price: float
    exit_price: float
    multiplier: int
    gross_pnl: float
    fees: float
    fee_commission: float
    fee_regulatory: float
    fee_slippage: float
    net_pnl: float
    exit_reason: ExitReason
    suppressed_reasons: tuple[ExitReason, ...]
    holding_days: int
    source: QuoteSource
    entry_context: Mapping[str, Maybe[float]]
    exit_context: Mapping[str, Maybe[float]]
    quote_gaps: int = 0
    deferred_days: int = 0
    #: Maximum favourable / adverse excursion over the holding period, as a
    #: fraction of the entry price. Only the event-driven engine can observe
    #: these — the trade table does not step day by day. Records from the trade
    #: table carry NaN, so downstream code raises rather than reading them as 0.
    mfe: float = float("nan")
    mae: float = float("nan")
    #: The same two in **underlying terms**: the furthest the underlying travelled for
    #: and against the position, as a fraction of the entry spot.
    #:
    #: Both are kept because they answer different questions. A premium excursion mixes
    #: the underlying's move with IV and theta, so a trade whose underlying went the right
    #: way can still show a negative premium MFE — which is the real behaviour of a long
    #: option and exactly what a premium stop reacts to. The underlying version isolates
    #: the move itself, and being an underlying-side quantity it is the one that **survives
    #: a synthetic chain**: the premium version there describes Black-Scholes.
    #:
    #: Sign convention: favourable means the direction the position profits from — up for
    #: a long call and a short put, down for a long put and a short call. Getting this
    #: backwards would silently swap MFE and MAE, so it is computed from the contract's
    #: right and the entry side rather than from the premium's own movement.
    mfe_underlying: float = float("nan")
    mae_underlying: float = float("nan")
    #: The grouping tag and note attached at order time (from the Plan). The
    #: classification reflects the judgement actually made that day; it is not
    #: assigned afterwards from the outcome, which would let the analysis
    #: conclude whatever it set out to.
    tag: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.exit_date < self.entry_date:
            raise ValueError("the exit date must not precede the entry date")
        # The record's own arithmetic, checked here rather than at each engine's call
        # site: both engines construct records, and a check either of them could forget
        # is not a mechanism. The ledger invariants cannot run in the trade table -- it
        # has no cash, positions or equity by construction -- so this is the part of the
        # invariant set that genuinely applies to both.
        trade_record_balances(self)
        object.__setattr__(self, "entry_context", MappingProxyType(dict(self.entry_context)))
        object.__setattr__(self, "exit_context", MappingProxyType(dict(self.exit_context)))

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0


#: Column order when exporting trades; both engines must agree.
TRADE_COLUMNS: tuple[str, ...] = (
    "trade_id",
    "engine",
    "symbol",
    "entry_date",
    "exit_date",
    "contract",
    "quantity",
    "entry_price",
    "exit_price",
    "multiplier",
    "gross_pnl",
    "fees",
    "fee_commission",
    "fee_regulatory",
    "fee_slippage",
    "net_pnl",
    "exit_reason",
    "suppressed_reasons",
    "holding_days",
    "source",
    "quote_gaps",
    "deferred_days",
    "mfe",
    "mae",
    "mfe_underlying",
    "mae_underlying",
    "tag",
    "note",
)


def require(condition: bool, message: str) -> None:
    """A startup check: raise :class:`StartupError` when it does not hold."""
    if not condition:
        raise StartupError(message)
