"""The portfolio ledger: cash, positions, daily marks, expiry settlement.

The trade-table engine computes each trade independently and never knows how much
money is in the account at any moment. This layer supplies that, which is what
makes the equity curve, drawdown, capital constraints and concurrency limits
possible at all.

Three rules run through it:

1. **Cash and positions are always consistent.** Every fill changes both and is
   checked immediately afterwards; an inconsistency aborts, because it is a code
   bug and continuing only produces wrong conclusions.
2. **Two equity curves.** Marking at the mid is the book value; marking at the
   liquidation price (bid for longs, ask for shorts) is what could actually be
   sold. Option spreads are wide, so the two can differ by several percentage
   points of max drawdown, and reporting only one is selective presentation.
3. **Settlement comes from the contract spec.** US ETF options settle
   physically — held to expiry in the money they deliver shares, which have to be
   paid for and cannot be sold until the next session. Modelling them as cash
   settled flatters performance systematically.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Iterator

from backtest.costs.model import FeeBreakdown
from backtest.errors import InvariantError
from backtest.maybe import Maybe
from backtest.types import ContractId, ContractSpec, Right, SettlementStyle, Side

#: Tolerance for the accounting identity. Floating-point accumulation over a few
#: thousand trades drifts in the last digits; anything larger is a real error.
TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class Position:
    """A position in one contract. Positive ``quantity`` is long, negative short."""

    contract: ContractId
    quantity: int
    avg_price: float          # weighted average cost (premium per share, positive)
    opened: date
    trade_id: int
    multiplier: int = 100

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def abs_quantity(self) -> int:
        return abs(self.quantity)

    @property
    def cost_basis(self) -> float:
        """The notional at entry (positive). For a short this is premium received."""
        return self.avg_price * self.abs_quantity * self.multiplier

    def market_value(self, price: float) -> float:
        """Market value at the given price. Negative for a short — it is a liability."""
        return price * self.quantity * self.multiplier


@dataclass(frozen=True, slots=True)
class StockPosition:
    """A stock position arising from exercise or assignment."""

    symbol: str
    shares: int
    avg_price: float
    acquired: date
    trade_id: int

    def market_value(self, price: float) -> float:
        return price * self.shares


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """One thing that happened on the ledger. All of them are kept, to be traced."""

    when: date
    kind: str            # open / close / expire_worthless / assign / exercise / liquidate
    detail: str
    cash_delta: float
    trade_id: int | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The portfolio's state after one session's close."""

    day: date
    cash: float
    option_value_mid: float
    option_value_liq: float
    stock_value: float
    equity_mid: float
    equity_liq: float
    n_positions: int
    n_stock: int
    deployed: float          # capital tied up (entry cost plus collateral)
    stale_marks: int         # positions marked with a stale quote


class Ledger:
    """The ledger. Only this may change cash and positions."""

    def __init__(self, initial_cash: float, spec: ContractSpec) -> None:
        if initial_cash <= 0:
            raise ValueError("initial cash must be positive")
        self._cash = float(initial_cash)
        self.initial_cash = float(initial_cash)
        self.spec = spec
        self._positions: dict[ContractId, Position] = {}
        self._stock: dict[str, StockPosition] = {}
        self.events: list[LedgerEvent] = []
        #: Cumulative fees, for the accounting identity.
        self.total_fees = 0.0
        #: Cumulative realised P&L.
        self.realised = 0.0

    # ── queries ───────────────────────────────────────────
    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> tuple[Position, ...]:
        return tuple(self._positions.values())

    @property
    def stock_positions(self) -> tuple[StockPosition, ...]:
        return tuple(self._stock.values())

    def position(self, contract: ContractId) -> Position | None:
        return self._positions.get(contract)

    def __len__(self) -> int:
        return len(self._positions)

    def __iter__(self) -> Iterator[Position]:
        return iter(self._positions.values())

    # ── fills ─────────────────────────────────────────────
    def apply_fill(
        self,
        *,
        contract: ContractId,
        side: Side,
        quantity: int,
        price: float,
        fees: FeeBreakdown,
        when: date,
        trade_id: int,
    ) -> float:
        """Apply one fill and return its realised P&L (0 when opening).

        The direction comes from ``side`` and has nothing to do with whether the
        position is long or short — a sell may close a long or open a short.
        """
        if quantity <= 0:
            raise ValueError("the fill quantity must be positive")
        multiplier = self.spec.multiplier
        notional = price * quantity * multiplier
        signed = quantity if side is Side.BUY else -quantity

        existing = self._positions.get(contract)
        realised = 0.0

        if existing is None:
            # Opening: a buy pays premium, a sell receives it.
            self._cash += -notional if side is Side.BUY else notional
            self._positions[contract] = Position(
                contract=contract, quantity=signed, avg_price=price,
                opened=when, trade_id=trade_id, multiplier=multiplier,
            )
            self._record(when, "open", f"{contract} {side.value} {quantity}", -notional if side is Side.BUY else notional, trade_id)
        elif (existing.quantity > 0) == (side is Side.BUY):
            # Adding in the same direction: merge at a weighted average cost.
            new_qty = existing.quantity + signed
            total = existing.avg_price * existing.abs_quantity + price * quantity
            self._cash += -notional if side is Side.BUY else notional
            self._positions[contract] = replace(
                existing, quantity=new_qty, avg_price=total / abs(new_qty)
            )
            self._record(when, "open", f"{contract} added {quantity}", -notional if side is Side.BUY else notional, trade_id)
        else:
            # Opposite direction: closing, possibly in part.
            closing = min(quantity, existing.abs_quantity)
            if existing.quantity > 0:            # closing a long: selling
                realised = (price - existing.avg_price) * closing * multiplier
                self._cash += price * closing * multiplier
            else:                                 # closing a short: buying back
                realised = (existing.avg_price - price) * closing * multiplier
                self._cash -= price * closing * multiplier
            self.realised += realised
            remaining = existing.quantity + (closing if existing.quantity < 0 else -closing)
            if remaining == 0:
                del self._positions[contract]
            else:
                # Partial close: the unit cost is unchanged, only the quantity falls.
                self._positions[contract] = replace(existing, quantity=remaining)
            self._record(when, "close",
                         f"{contract} closed {closing}, realised {realised:+.2f}",
                         price * closing * multiplier * (1 if existing.quantity > 0 else -1), trade_id)
            if quantity > closing:
                raise InvariantError(
                    f"the fill quantity {quantity} for {contract} exceeds the position "
                    f"of {existing.abs_quantity}; reversing through zero is not supported"
                )

        self._cash -= fees.total
        self.total_fees += fees.total
        return realised

    # ── expiry ────────────────────────────────────────────
    def settle_expiry(
        self, contract: ContractId, spot: Maybe[float], when: date, *, auto_exercise_threshold: float = 0.01
    ) -> str:
        """Settle at expiry. Returns how it settled, for the caller's exit reason."""
        pos = self._positions.get(contract)
        if pos is None:
            raise KeyError(f"{contract} is not held")
        if spot.is_missing:
            raise InvariantError(f"no underlying close for {contract} on its expiry "
                                 f"{when}, so it cannot be settled: {spot.reason}")

        s = spot.unwrap()
        intrinsic = (
            max(s - contract.strike, 0.0) if contract.right is Right.CALL
            else max(contract.strike - s, 0.0)
        )
        del self._positions[contract]

        if intrinsic < auto_exercise_threshold:
            self.realised += -pos.cost_basis if pos.quantity > 0 else pos.cost_basis
            self._record(when, "expire_worthless",
                         f"{contract} expired out of the money, spot {s:.2f}", 0.0,
                         pos.trade_id)
            return "worthless"

        if self.spec.settlement is SettlementStyle.CASH:
            flow = intrinsic * pos.quantity * pos.multiplier
            self._cash += flow
            self.realised += flow - (pos.cost_basis if pos.quantity > 0 else -pos.cost_basis)
            self._record(when, "settle_cash",
                         f"{contract} settled in cash in the money, intrinsic "
                         f"{intrinsic:.2f}", flow, pos.trade_id)
            return "cash"

        # Physical settlement: shares change hands at the strike.
        # The option's own P&L is **fully realised** at this moment — the contract
        # is gone and the premium has been made or lost. The shares are held
        # separately at the strike as their cost, and their P&L is realised when
        # they are disposed of.
        # Omit this step and the accounting identity reports it at once.
        self.realised += -pos.cost_basis if pos.quantity > 0 else pos.cost_basis
        shares = pos.quantity * pos.multiplier
        if contract.right is Right.PUT:
            shares = -shares            # long a put sells shares, short a put buys
        cash_flow = -shares * contract.strike
        self._cash += cash_flow
        self._add_stock(contract.symbol, shares, contract.strike, when, pos.trade_id)
        self._record(when, "assign" if pos.quantity < 0 else "exercise",
                     f"{contract} settled physically, {shares:+d} shares @ "
                     f"{contract.strike:.2f}",
                     cash_flow, pos.trade_id)
        return "physical"

    def _add_stock(self, symbol: str, shares: int, price: float, when: date, trade_id: int) -> None:
        if shares == 0:
            return
        cur = self._stock.get(symbol)
        if cur is None:
            self._stock[symbol] = StockPosition(symbol, shares, price, when, trade_id)
            return
        total = cur.shares + shares
        if total == 0:
            self.realised += (price - cur.avg_price) * (-shares)
            del self._stock[symbol]
            return
        blended = (cur.avg_price * cur.shares + price * shares) / total
        self._stock[symbol] = replace(cur, shares=total, avg_price=blended)

    def liquidate_stock(self, symbol: str, price: float, when: date, fees: FeeBreakdown) -> float:
        """Dispose of a stock position. Shares arriving from exercise should not be
        kept — that is no longer an option strategy."""
        pos = self._stock.get(symbol)
        if pos is None:
            raise KeyError(f"no stock position in {symbol}")
        proceeds = price * pos.shares
        realised = (price - pos.avg_price) * pos.shares
        self._cash += proceeds - fees.total
        self.total_fees += fees.total
        self.realised += realised
        del self._stock[symbol]
        self._record(when, "liquidate",
                     f"{symbol} closed {pos.shares:+d} shares @ {price:.2f}, "
                     f"realised {realised:+.2f}",
                     proceeds, pos.trade_id)
        return realised

    # ── marking ───────────────────────────────────────────
    def snapshot(
        self,
        day: date,
        quotes: dict[ContractId, tuple[float, float]],
        spot: Maybe[float],
        *,
        stale: dict[ContractId, bool] | None = None,
    ) -> Snapshot:
        """Mark to the close. ``quotes`` is {contract: (bid, ask)}."""
        stale = stale or {}
        mid_total = liq_total = deployed = 0.0
        missing = 0
        for pos in self._positions.values():
            quote = quotes.get(pos.contract)
            if quote is None:
                missing += 1
                mid = liq = pos.avg_price          # no quote: mark at cost, count it stale
            else:
                bid, ask = quote[0], quote[1]   # the tuple may carry the whole row
                mid = (bid + ask) / 2.0
                liq = bid if pos.quantity > 0 else ask   # bid for longs, ask for shorts
            mid_total += pos.market_value(mid)
            liq_total += pos.market_value(liq)
            deployed += pos.cost_basis if pos.quantity > 0 else self._collateral(pos)

        stock_total = 0.0
        if self._stock:
            if spot.is_missing:
                raise InvariantError(f"stock is held on {day} but there is no "
                                     f"underlying close: {spot.reason}")
            stock_total = sum(p.market_value(spot.unwrap()) for p in self._stock.values())

        stale_count = missing + sum(1 for c, s in stale.items() if s and c in self._positions)
        return Snapshot(
            day=day, cash=self._cash,
            option_value_mid=mid_total, option_value_liq=liq_total,
            stock_value=stock_total,
            equity_mid=self._cash + mid_total + stock_total,
            equity_liq=self._cash + liq_total + stock_total,
            n_positions=len(self._positions), n_stock=len(self._stock),
            deployed=deployed, stale_marks=stale_count,
        )

    def _collateral(self, pos: Position) -> float:
        """Collateral tied up by a short.

        A cash-secured put is strike x shares. There is no margin model for a naked
        call, and offering the strike as a floor would simply be wrong — so the
        layer above must refuse to open a naked call rather than have a number
        invented here.
        """
        if pos.contract.right is Right.PUT:
            return pos.contract.strike * pos.abs_quantity * pos.multiplier
        return float("nan")          # deliberately NaN: any use of it shows up

    # ── the accounting identity ───────────────────────────
    def unrealised(self, snapshot: Snapshot) -> tuple[float, float]:
        """(unrealised option P&L, unrealised stock P&L).

        The sign on a short is easy to get backwards: selling received premium, so
        cash has already risen and the market value is **negative** (a liability).
        Its unrealised P&L is therefore "premium received plus current market
        value", equivalently "premium minus the cost of buying it back".
        """
        signed_cost = sum(
            p.cost_basis if p.quantity > 0 else -p.cost_basis
            for p in self._positions.values()
        )
        stock_cost = sum(p.avg_price * p.shares for p in self._stock.values())
        return (snapshot.option_value_mid - signed_cost, snapshot.stock_value - stock_cost)

    def check_invariant(self, snapshot: Snapshot) -> None:
        """The accounting identity:

            closing equity = opening cash + realised P&L − fees + unrealised P&L

        A breach means a code bug: abort at once, because continuing only produces
        wrong conclusions.
        """
        unreal_opt, unreal_stock = self.unrealised(snapshot)
        expected = (
            self.initial_cash + self.realised - self.total_fees + unreal_opt + unreal_stock
        )
        drift = snapshot.equity_mid - expected
        if abs(drift) > max(TOLERANCE, abs(expected) * 1e-9):
            raise InvariantError(
                f"the accounting identity fails on {snapshot.day}, off by {drift:+.6f}\n"
                f"  opening {self.initial_cash:,.2f}"
                f" + realised {self.realised:+,.2f}"
                f" − fees {self.total_fees:,.2f}"
                f" + unrealised options {unreal_opt:+,.2f}"
                f" + unrealised stock {unreal_stock:+,.2f}"
                f" = {expected:,.2f}, actual equity {snapshot.equity_mid:,.2f}"
            )

    def _record(self, when: date, kind: str, detail: str, cash_delta: float, trade_id: int | None) -> None:
        self.events.append(LedgerEvent(when, kind, detail, round(cash_delta, 6), trade_id))
