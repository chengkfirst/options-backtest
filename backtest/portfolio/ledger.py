"""组合账本：现金、持仓、逐日盯市、到期结算。

成交表引擎各笔独立计算，不知道任何时刻账上有多少钱。这一层补上那件事，
于是权益曲线、回撤、资金约束、并发上限才成立。

三条贯穿的规则：

1. **现金与持仓永远一致。** 每笔成交同时改两者，改完立刻校验；不一致直接中止，
   因为那是代码 bug，继续跑只会产出错误结论。
2. **两条权益曲线。** 中价盯市是"账面"，清算价盯市（多头按买价、空头按卖价）是
   "真卖得掉多少"。期权价差宽，两者的最大回撤可能差几个百分点，只报一条等于
   选择性呈现。
3. **交割方式来自合约规格。** 美股 ETF 期权是实物交割——实值到期拿到股票、要现金
   接货、周一才能卖。按现金结算建模会系统性高估绩效。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Iterator

from backtest.costs.model import FeeBreakdown
from backtest.errors import InvariantError
from backtest.maybe import Maybe
from backtest.types import ContractId, ContractSpec, Right, SettlementStyle, Side

#: 账目守恒的容差。浮点累加在几千笔交易后会有末位误差，超过这个量级就是真错了。
TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class Position:
    """一个合约的持仓。``quantity`` 正数为多头、负数为空头。"""

    contract: ContractId
    quantity: int
    avg_price: float          # 加权平均成本（每股权利金，正数）
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
        """建仓时的名义金额（正数）。空头这是收到的权利金。"""
        return self.avg_price * self.abs_quantity * self.multiplier

    def market_value(self, price: float) -> float:
        """按给定单价的市值。空头为负——它是负债。"""
        return price * self.quantity * self.multiplier


@dataclass(frozen=True, slots=True)
class StockPosition:
    """因行权或指派产生的股票头寸。"""

    symbol: str
    shares: int
    avg_price: float
    acquired: date
    trade_id: int

    def market_value(self, price: float) -> float:
        return price * self.shares


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """账本上发生的一件事。全部留痕，供事后追溯。"""

    when: date
    kind: str            # open / close / expire_worthless / assign / exercise / liquidate
    detail: str
    cash_delta: float
    trade_id: int | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """某交易日收盘后的组合状态。"""

    day: date
    cash: float
    option_value_mid: float
    option_value_liq: float
    stock_value: float
    equity_mid: float
    equity_liq: float
    n_positions: int
    n_stock: int
    deployed: float          # 被占用的资金（建仓成本 + 担保金）
    stale_marks: int         # 用了陈旧报价的持仓数


class Ledger:
    """账本。只有它能改现金与持仓。"""

    def __init__(self, initial_cash: float, spec: ContractSpec) -> None:
        if initial_cash <= 0:
            raise ValueError("初始资金须为正")
        self._cash = float(initial_cash)
        self.initial_cash = float(initial_cash)
        self.spec = spec
        self._positions: dict[ContractId, Position] = {}
        self._stock: dict[str, StockPosition] = {}
        self.events: list[LedgerEvent] = []
        #: 累计费用，用于账目守恒校验
        self.total_fees = 0.0
        #: 累计已实现盈亏
        self.realised = 0.0

    # ── 查询 ──────────────────────────────────────────────
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

    # ── 成交 ──────────────────────────────────────────────
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
        """应用一笔成交，返回这笔的已实现盈亏（开仓为 0）。

        方向由 ``side`` 决定，与持仓是多是空无关——卖出既可能是平多头，也可能是开空头。
        """
        if quantity <= 0:
            raise ValueError("成交数量须为正")
        multiplier = self.spec.multiplier
        notional = price * quantity * multiplier
        signed = quantity if side is Side.BUY else -quantity

        existing = self._positions.get(contract)
        realised = 0.0

        if existing is None:
            # 开新仓：买入付出权利金，卖出收到权利金
            self._cash += -notional if side is Side.BUY else notional
            self._positions[contract] = Position(
                contract=contract, quantity=signed, avg_price=price,
                opened=when, trade_id=trade_id, multiplier=multiplier,
            )
            self._record(when, "open", f"{contract} {side.value} {quantity}", -notional if side is Side.BUY else notional, trade_id)
        elif (existing.quantity > 0) == (side is Side.BUY):
            # 同向加仓：按加权平均成本合并
            new_qty = existing.quantity + signed
            total = existing.avg_price * existing.abs_quantity + price * quantity
            self._cash += -notional if side is Side.BUY else notional
            self._positions[contract] = replace(
                existing, quantity=new_qty, avg_price=total / abs(new_qty)
            )
            self._record(when, "open", f"{contract} 加仓 {quantity}", -notional if side is Side.BUY else notional, trade_id)
        else:
            # 反向：了结（可能部分）
            closing = min(quantity, existing.abs_quantity)
            if existing.quantity > 0:            # 平多头：卖出
                realised = (price - existing.avg_price) * closing * multiplier
                self._cash += price * closing * multiplier
            else:                                 # 平空头：买回
                realised = (existing.avg_price - price) * closing * multiplier
                self._cash -= price * closing * multiplier
            self.realised += realised
            remaining = existing.quantity + (closing if existing.quantity < 0 else -closing)
            if remaining == 0:
                del self._positions[contract]
            else:
                # 部分了结：单位成本不变，只减数量
                self._positions[contract] = replace(existing, quantity=remaining)
            self._record(when, "close", f"{contract} 了结 {closing}，已实现 {realised:+.2f}",
                         price * closing * multiplier * (1 if existing.quantity > 0 else -1), trade_id)
            if quantity > closing:
                raise InvariantError(
                    f"{contract} 的成交数量 {quantity} 超过持仓 {existing.abs_quantity}，"
                    f"反手开仓未被支持"
                )

        self._cash -= fees.total
        self.total_fees += fees.total
        return realised

    # ── 到期 ──────────────────────────────────────────────
    def settle_expiry(
        self, contract: ContractId, spot: Maybe[float], when: date, *, auto_exercise_threshold: float = 0.01
    ) -> str:
        """到期结算。返回结算方式，供上层记录了结原因。"""
        pos = self._positions.get(contract)
        if pos is None:
            raise KeyError(f"{contract} 不在持仓中")
        if spot.is_missing:
            raise InvariantError(f"{contract} 到期日 {when} 没有标的收盘价，无法结算：{spot.reason}")

        s = spot.unwrap()
        intrinsic = (
            max(s - contract.strike, 0.0) if contract.right is Right.CALL
            else max(contract.strike - s, 0.0)
        )
        del self._positions[contract]

        if intrinsic < auto_exercise_threshold:
            self.realised += -pos.cost_basis if pos.quantity > 0 else pos.cost_basis
            self._record(when, "expire_worthless",
                         f"{contract} 虚值到期，标的 {s:.2f}", 0.0, pos.trade_id)
            return "worthless"

        if self.spec.settlement is SettlementStyle.CASH:
            flow = intrinsic * pos.quantity * pos.multiplier
            self._cash += flow
            self.realised += flow - (pos.cost_basis if pos.quantity > 0 else -pos.cost_basis)
            self._record(when, "settle_cash",
                         f"{contract} 实值现金结算，内在价值 {intrinsic:.2f}", flow, pos.trade_id)
            return "cash"

        # 实物交割：按行权价交收股票。
        # 期权本身的盈亏在此刻**全部实现**——合约消失了，权利金已经赚到或亏掉；
        # 股票以行权价为成本单独持有，它自己的盈亏在处置时才实现。
        # 漏掉这一步的话，账目守恒校验会立刻报出来（曾经确实漏过）。
        self.realised += -pos.cost_basis if pos.quantity > 0 else pos.cost_basis
        shares = pos.quantity * pos.multiplier
        if contract.right is Right.PUT:
            shares = -shares            # put 的多头是卖出股票、空头是买入股票
        cash_flow = -shares * contract.strike
        self._cash += cash_flow
        self._add_stock(contract.symbol, shares, contract.strike, when, pos.trade_id)
        self._record(when, "assign" if pos.quantity < 0 else "exercise",
                     f"{contract} 实物交割，{shares:+d} 股 @ {contract.strike:.2f}",
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
        """处置股票头寸。行权衍生的持股不该留着——那已经不是期权策略了。"""
        pos = self._stock.get(symbol)
        if pos is None:
            raise KeyError(f"没有 {symbol} 的股票头寸")
        proceeds = price * pos.shares
        realised = (price - pos.avg_price) * pos.shares
        self._cash += proceeds - fees.total
        self.total_fees += fees.total
        self.realised += realised
        del self._stock[symbol]
        self._record(when, "liquidate",
                     f"{symbol} 平掉 {pos.shares:+d} 股 @ {price:.2f}，已实现 {realised:+.2f}",
                     proceeds, pos.trade_id)
        return realised

    # ── 盯市 ──────────────────────────────────────────────
    def snapshot(
        self,
        day: date,
        quotes: dict[ContractId, tuple[float, float]],
        spot: Maybe[float],
        *,
        stale: dict[ContractId, bool] | None = None,
    ) -> Snapshot:
        """收盘盯市。``quotes`` 是 {合约: (bid, ask)}。"""
        stale = stale or {}
        mid_total = liq_total = deployed = 0.0
        missing = 0
        for pos in self._positions.values():
            quote = quotes.get(pos.contract)
            if quote is None:
                missing += 1
                mid = liq = pos.avg_price          # 无报价时按成本估，并计入陈旧计数
            else:
                bid, ask = quote[0], quote[1]   # 报价元组可能还带着整行，只取前两项
                mid = (bid + ask) / 2.0
                liq = bid if pos.quantity > 0 else ask   # 多头按买价、空头按卖价
            mid_total += pos.market_value(mid)
            liq_total += pos.market_value(liq)
            deployed += pos.cost_basis if pos.quantity > 0 else self._collateral(pos)

        stock_total = 0.0
        if self._stock:
            if spot.is_missing:
                raise InvariantError(f"{day} 持有股票头寸但没有标的收盘价：{spot.reason}")
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
        """空头占用的担保金。

        现金担保 put = 行权价 × 股数。裸卖 call 的保证金模型没做，这里给出行权价
        作为下限并不正确——所以上层必须在开仓前拒绝裸卖 call，而不是让这里编一个数。
        """
        if pos.contract.right is Right.PUT:
            return pos.contract.strike * pos.abs_quantity * pos.multiplier
        return float("nan")          # 故意是 NaN：任何用到它的地方都会显形

    # ── 守恒 ──────────────────────────────────────────────
    def unrealised(self, snapshot: Snapshot) -> tuple[float, float]:
        """（期权浮动盈亏，股票浮动盈亏）。

        空头的符号容易搞反：卖出时收到权利金、现金已经增加，市值是**负数**（负债）。
        所以它的浮动盈亏是"收到的权利金 + 当前市值"，等价于"权利金 − 买回要花的钱"。
        """
        signed_cost = sum(
            p.cost_basis if p.quantity > 0 else -p.cost_basis
            for p in self._positions.values()
        )
        stock_cost = sum(p.avg_price * p.shares for p in self._stock.values())
        return (snapshot.option_value_mid - signed_cost, snapshot.stock_value - stock_cost)

    def check_invariant(self, snapshot: Snapshot) -> None:
        """账目守恒：

            期末权益 = 期初现金 + 已实现盈亏 − 费用 + 浮动盈亏

        不守恒说明代码有 bug，立即中止——继续跑只会产出错误结论。这条校验在开发中
        抓到过一次真错：实物交割时权利金没有计入已实现。
        """
        unreal_opt, unreal_stock = self.unrealised(snapshot)
        expected = (
            self.initial_cash + self.realised - self.total_fees + unreal_opt + unreal_stock
        )
        drift = snapshot.equity_mid - expected
        if abs(drift) > max(TOLERANCE, abs(expected) * 1e-9):
            raise InvariantError(
                f"{snapshot.day} 账目不守恒，偏差 {drift:+.6f}\n"
                f"  期初 {self.initial_cash:,.2f}"
                f" + 已实现 {self.realised:+,.2f}"
                f" − 费用 {self.total_fees:,.2f}"
                f" + 期权浮动 {unreal_opt:+,.2f}"
                f" + 股票浮动 {unreal_stock:+,.2f}"
                f" = {expected:,.2f}，实际权益 {snapshot.equity_mid:,.2f}"
            )

    def _record(self, when: date, kind: str, detail: str, cash_delta: float, trade_id: int | None) -> None:
        self.events.append(LedgerEvent(when, kind, detail, round(cash_delta, 6), trade_id))
