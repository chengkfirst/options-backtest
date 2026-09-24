"""成交表引擎：以表操作产出交易明细，而非逐日模拟。

核心洞察：**一笔有明确进出场规则的期权交易，本质是一次查表，不是一次模拟。**
止盈止损常被误认为必须逐 bar 推进——实际上它需要的只是该合约在持有窗口内的逐日
报价序列，取第一个越过阈值的日子即可。一次切片取代整个循环。

**时点约定贯穿始终**：任何决策发生在 T 日收盘，对应成交在 T+lag 日收盘。入场如此，
了结也如此。用 T 日收盘价同时做决策和成交是最常见的前视来源，这里在架构上排除。

**口径**：各笔交易独立计算，不考虑资金约束、不考虑并发上限、不产生组合权益曲线。
因此它回答的是"这个想法有没有 edge"，不是"这个策略能不能跑"。并发持仓数序列被一并
输出，使资金可行性的缺口从看不见变成一眼可见。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

import numpy as np
import pandas as pd

from backtest.config import BacktestConfig
from backtest.costs.model import (
    FeeBreakdown,
    ZERO_FEES,
    check_tradable,
    execute,
)
from backtest.data.chain import ChainSource
from backtest.data.market import MarketData
from backtest.engine.result import TradeTableResult
from backtest.engine.strategy import Strategy
from backtest.errors import InvariantError, StartupError
from backtest.events import EventKind, EventLog
from backtest.maybe import Maybe
from backtest.selection.rules import SelectionResult, select_contract
from backtest.signals.registry import SignalSet
from backtest.types import (
    ContractId,
    ExitReason,
    QuoteSource,
    Right,
    Side,
    TradeRecord,
    resolve_exit_reason,
)

ENGINE_NAME = "trade_table"


@dataclass(frozen=True, slots=True)
class _ExitDecision:
    day: date
    reasons: tuple[ExitReason, ...]


class TradeTableEngine:
    """七步流程：枚举 → 选约 → 入场报价 → 切片 → 算比例 → 首次触发 → 了结。"""

    def __init__(
        self,
        market: MarketData,
        chain: ChainSource,
        signals: SignalSet,
        config: BacktestConfig | None = None,
        events: EventLog | None = None,
    ) -> None:
        self.market = market
        self.chain = chain
        self.signals = signals
        self.config = config or market.config
        self.events = events or market.events

    # ---- 守卫 ----
    #: 本引擎不支持的策略特性。给近似结果是最坏的选项——使用者拿到一个看起来正常的
    #: 数字，却不知道它对应的规则并没有被真正执行。
    UNSUPPORTED = {
        "roll": "展期：新合约依赖旧合约何时退出，各笔独立计算表达不了",
        "sizing_by_equity": "按权益比例定规模：各笔独立计算，不存在'当前权益'",
        "portfolio_limits": "组合层敞口限额：需要跨持仓的状态",
        "hedge": "跨持仓对冲：需要组合层视角",
        "max_concurrent": "并发上限：各笔独立计算，互不知晓",
    }

    def _guard(self, strategy: Strategy) -> None:
        """拒绝本引擎无法正确处理的配置，而不是给出近似结果。"""
        if strategy.review is not None:
            raise StartupError(
                "逐日复查只能用事件驱动引擎。成交表引擎向量化算出场——一次性算完"
                "整个持有窗口的首次触发，根本没有「逐日」这个环节。"
                "改用 engine='event_driven'。"
            )
        if strategy.plan is not None:
            raise StartupError(
                "计划式策略只能用事件驱动引擎。成交表引擎各笔独立、向量化算出场，"
                "表达不了'这一笔用这套出场规则'——而那正是计划式的要点。"
                "改用 engine='event_driven'。"
            )
        self.signals.require(list(strategy.entry.requires))
        if strategy.exit.hold_days is None and strategy.exit.dte_floor is None:
            raise StartupError("缺少时间类了结条件，仓位会被拖到到期")

        # 执行延迟一天意味着：剩余 dte_floor 日那天做决策，剩余 dte_floor-1 日那天成交。
        # 下限小于 lag+1 时成交日落在到期日或之后，了结意图会落空，仓位被静默拖到结算。
        lag = self.config.execution_lag_days
        floor = strategy.exit.dte_floor
        if floor is not None and floor < lag + 1:
            raise StartupError(
                f"剩余到期下限 {floor} 个交易日过小：执行延迟为 {lag} 天，"
                f"决策日之后还要 {lag} 天才成交，下限至少为 {lag + 1}。"
                f"否则了结意图会落空，仓位被静默拖到到期结算。"
            )
        if strategy.sizing.method != "fixed":
            raise StartupError(
                f"成交表引擎只支持固定张数：各笔独立计算，不存在\"当前权益\"，"
                f"因此 {strategy.sizing.method} 无从算起。请改用 engine=\"event_driven\"。"
            )
        for feature, why in self.UNSUPPORTED.items():
            if getattr(strategy, feature, None):
                raise StartupError(
                    f"成交表引擎不支持 {feature}（{why}）；该策略需要事件驱动引擎"
                    f"（见 ROADMAP.md 阶段 4）"
                )

    # ---- 主流程 ----
    def run(self, strategy: Strategy) -> "TradeTableResult":
        self._guard(strategy)
        sessions = self.market.sessions()
        records: list[TradeRecord] = []
        trade_id = 0

        #: 已接受的交易，用来判断"这张合约此刻是否已在持仓"。
        #: 成交表本来是无状态的，这是唯一一处例外——为的是与事件驱动引擎语义一致。
        held: list[TradeRecord] = []

        for signal_day in sessions:
            view = self.signals.on(signal_day)
            if not strategy.entry.condition(view):
                continue

            entry_day = self.market.calendar.shift(signal_day, self.config.execution_lag_days)
            if entry_day is None or entry_day > self.config.end:
                self.events.record(
                    EventKind.CANDIDATE_DROPPED, signal_day, "执行日超出回测区间"
                )
                continue

            record = self._build_trade(trade_id + 1, signal_day, entry_day, strategy)
            if record is None:
                continue
            if not strategy.allow_stacking and any(
                r.contract == record.contract and r.entry_date <= entry_day < r.exit_date
                for r in held
            ):
                self.events.record(
                    EventKind.CANDIDATE_DROPPED, entry_day,
                    "同一合约已在持仓中，allow_stacking 为假", str(record.contract))
                continue
            trade_id += 1
            record = replace(record, trade_id=trade_id)
            records.append(record)
            held.append(record)

        return TradeTableResult(
            records=tuple(records),
            config=self.config,
            strategy=strategy,
            events=self.events,
            calendar_sessions=sessions,
        )

    # ---- 单笔 ----
    def _build_trade(
        self, trade_id: int, signal_day: date, entry_day: date, strategy: Strategy
    ) -> TradeRecord | None:
        selection = select_contract(
            self.chain.snapshot(entry_day),
            strategy.select,
            self.config,
            self.market.calendar,
            entry_day,
            # 链取执行日的，信号视图取**决策日**的。自定义选约函数可以用 T 日看到的
            # 东西决定"要一张什么样的"，但不许看 T+1 的行情——那等于拿成交日的涨跌
            # 反过来挑行权价。
            self.signals.on(signal_day),
        )
        if not selection.ok:
            self.events.record(
                EventKind.SELECTION_FAILED, entry_day, selection.describe()
            )
            return None

        contract = selection.contract.unwrap()
        entry_quote = selection.quote
        assert entry_quote is not None
        tradable = check_tradable(entry_quote["bid"], entry_quote["ask"], self.config.costs)
        if not tradable.ok:
            self.events.record(
                EventKind.NOT_TRADABLE, entry_day, f"入场不可成交：{tradable.reason}", str(contract)
            )
            return None

        entry_price, entry_fees = execute(
            bid=entry_quote["bid"],
            ask=entry_quote["ask"],
            side=Side.BUY,
            quantity=strategy.quantity,
            multiplier=self.config.contract.multiplier,
            costs=self.config.costs,
        )

        window = self._holding_window(entry_day, contract, strategy)
        quotes = self.chain.series(contract, window)
        gaps = len(window) - len(quotes)
        if gaps:
            self.events.record(
                EventKind.QUOTE_MISSING, entry_day, f"持有窗口内缺 {gaps} 天报价", str(contract)
            )

        decision = self._first_trigger(entry_day, entry_price, contract, quotes, strategy, window)
        return self._close_trade(
            trade_id=trade_id,
            contract=contract,
            entry_day=entry_day,
            entry_price=entry_price,
            entry_fees=entry_fees,
            entry_quote=entry_quote,
            decision=decision,
            quotes=quotes,
            strategy=strategy,
            gaps=gaps,
        )

    def _holding_window(self, entry_day: date, contract: ContractId, strategy: Strategy) -> list[date]:
        """报价窗口：入场日起、到期日止。

        窗口比**决策**窗口多出一个执行延迟——决策发生在某日收盘，成交在其后一个
        交易日收盘，成交那天的报价必须也在这一次切片里，否则每笔交易都要多发一次
        查询，且窗口边界与执行边界不一致。
        """
        cal = self.market.calendar
        last = contract.expiry
        if strategy.exit.hold_days is not None:
            capped = cal.shift(entry_day, strategy.exit.hold_days + self.config.execution_lag_days)
            if capped is not None:
                last = min(last, capped)
        return [d for d in cal.sessions_in(entry_day, last)]

    def _decision_window(self, window: list[date], entry_day: date, strategy: Strategy) -> list[date]:
        """决策窗口：报价窗口去掉末尾为执行预留的那几天。"""
        cal = self.market.calendar
        if strategy.exit.hold_days is None:
            return [d for d in window if d > entry_day]
        limit = cal.shift(entry_day, strategy.exit.hold_days)
        return [d for d in window if entry_day < d <= (limit or window[-1])]

    def _exit_price_of(self, row: pd.Series) -> Maybe[float]:
        """了结价：卖出方向，取不利端（bid）。"""
        tradable = check_tradable(row["bid"], row["ask"], self.config.costs)
        if not tradable.ok:
            return Maybe.missing(tradable.reason or "不可成交")
        from backtest.costs.model import fill_price

        return Maybe.some(fill_price(row["bid"], row["ask"], Side.SELL, self.config.costs))

    def _first_trigger(
        self,
        entry_day: date,
        entry_price: float,
        contract: ContractId,
        quotes: pd.DataFrame,
        strategy: Strategy,
        window: list[date],
    ) -> _ExitDecision:
        """在持有窗口内找**最早触发**的了结条件。

        决策发生在某日收盘，因此这里返回的是"决策日"；实际成交在决策日 + 执行延迟。
        """
        rule = strategy.exit
        cal = self.market.calendar
        if quotes.empty:
            return _ExitDecision(entry_day, (ExitReason.TIME_EXIT,))

        decision_days = set(self._decision_window(window, entry_day, strategy))
        asof = quotes["asof"].map(lambda x: x if isinstance(x, date) else pd.Timestamp(x).date())
        frame = quotes.loc[asof.isin(decision_days)].copy()
        if frame.empty:
            return _ExitDecision(entry_day, (ExitReason.TIME_EXIT,))

        exit_prices = frame.apply(self._exit_price_of, axis=1)
        ratios = np.array(
            [
                (p.unwrap() / entry_price - 1.0) if p.is_present else np.nan
                for p in exit_prices
            ]
        )
        days = [d if isinstance(d, date) else pd.Timestamp(d).date() for d in frame["asof"]]

        triggers: list[tuple[int, ExitReason]] = []
        if rule.take_profit is not None:
            hit = np.where(ratios >= rule.take_profit)[0]
            if hit.size:
                triggers.append((int(hit[0]), ExitReason.TAKE_PROFIT))
        if rule.stop_loss is not None:
            hit = np.where(ratios <= -rule.stop_loss)[0]
            if hit.size:
                triggers.append((int(hit[0]), ExitReason.STOP_LOSS))
        if rule.dte_floor is not None:
            remaining = np.array([cal.sessions_between(d, contract.expiry) for d in days])
            hit = np.where(remaining <= rule.dte_floor)[0]
            if hit.size:
                triggers.append((int(hit[0]), ExitReason.DTE_FLOOR))
        if rule.hold_days is not None:
            held = np.array([cal.sessions_between(entry_day, d) for d in days])
            hit = np.where(held >= rule.hold_days)[0]
            if hit.size:
                triggers.append((int(hit[0]), ExitReason.TIME_EXIT))

        if not triggers:
            # 没有任何条件触发：窗口末日即决策日
            return _ExitDecision(days[-1], (ExitReason.TIME_EXIT,))

        earliest = min(idx for idx, _ in triggers)
        same_day = tuple(reason for idx, reason in triggers if idx == earliest)
        return _ExitDecision(days[earliest], same_day)

    def _close_trade(
        self,
        *,
        trade_id: int,
        contract: ContractId,
        entry_day: date,
        entry_price: float,
        entry_fees: FeeBreakdown,
        entry_quote: dict,
        decision: _ExitDecision,
        quotes: pd.DataFrame,
        strategy: Strategy,
        gaps: int,
    ) -> TradeRecord | None:
        cal = self.market.calendar
        lag = self.config.execution_lag_days
        multiplier = self.config.contract.multiplier
        qty = strategy.quantity

        settle_day = cal.shift(decision.day, lag)
        reasons = list(decision.reasons)
        deferred = 0
        exit_price: float | None = None
        exit_row: pd.Series | None = None

        # 决策日之后按执行延迟找可成交的日子；不可成交则顺延
        candidates = [d for d in cal.sessions_in(settle_day or decision.day, contract.expiry)] if settle_day else []
        for day in candidates:
            if day > self.config.end:
                break
            row = quotes.loc[quotes["asof"].map(lambda x: (x if isinstance(x, date) else pd.Timestamp(x).date())) == day]
            if row.empty:
                fresh = self.chain.series(contract, [day])
                if fresh.empty:
                    deferred += 1
                    continue
                row = fresh
            price = self._exit_price_of(row.iloc[0])
            if price.is_present:
                exit_price = price.unwrap()
                exit_row = row.iloc[0]
                exit_day = day
                break
            deferred += 1

        if exit_price is None:
            # 至到期仍未能成交 → 被动到期，按内在价值结算
            return self._settle_at_expiry(
                trade_id=trade_id,
                contract=contract,
                entry_day=entry_day,
                entry_price=entry_price,
                entry_fees=entry_fees,
                entry_quote=entry_quote,
                strategy=strategy,
                gaps=gaps,
                deferred=deferred,
            )

        if deferred:
            self.events.record(
                EventKind.EXIT_DEFERRED, decision.day, f"了结顺延 {deferred} 个交易日", str(contract)
            )

        _, exit_fees = execute(
            bid=exit_row["bid"],
            ask=exit_row["ask"],
            side=Side.SELL,
            quantity=qty,
            multiplier=multiplier,
            costs=self.config.costs,
        )
        return self._record(
            trade_id=trade_id,
            contract=contract,
            entry_day=entry_day,
            exit_day=exit_day,
            entry_price=entry_price,
            exit_price=exit_price,
            fees=entry_fees + exit_fees,
            reason=resolve_exit_reason(reasons),
            suppressed=tuple(r for r in reasons if r is not resolve_exit_reason(reasons)),
            entry_quote=entry_quote,
            exit_quote=exit_row.to_dict(),
            strategy=strategy,
            gaps=gaps,
            deferred=deferred,
        )

    def _settle_at_expiry(
        self, *, trade_id, contract, entry_day, entry_price, entry_fees, entry_quote, strategy, gaps, deferred
    ) -> TradeRecord | None:
        spot = self.market.spot(contract.expiry)
        if spot.is_missing:
            self.events.record(
                EventKind.QUOTE_MISSING, contract.expiry, "到期日无标的收盘价，该笔作废", str(contract)
            )
            return None
        s = spot.unwrap()
        intrinsic = (
            max(s - contract.strike, 0.0)
            if contract.right is Right.CALL
            else max(contract.strike - s, 0.0)
        )
        itm = intrinsic > 0
        reason = ExitReason.EXPIRY_ITM if itm else ExitReason.EXPIRY_OTM
        self.events.record(
            EventKind.PASSIVE_EXPIRY,
            contract.expiry,
            f"被动到期（{'实值' if itm else '虚值'}），内在价值 {intrinsic:.2f}",
            str(contract),
        )
        exit_quote = dict(entry_quote)
        exit_quote.update({"asof": contract.expiry, "underlying_price": s, "bid": intrinsic, "ask": intrinsic})
        return self._record(
            trade_id=trade_id,
            contract=contract,
            entry_day=entry_day,
            exit_day=contract.expiry,
            entry_price=entry_price,
            exit_price=intrinsic,
            fees=entry_fees,
            reason=reason,
            suppressed=(),
            entry_quote=entry_quote,
            exit_quote=exit_quote,
            strategy=strategy,
            gaps=gaps,
            deferred=deferred,
        )

    def _context(self, quote: dict, day: date, contract: ContractId) -> dict[str, Maybe[float]]:
        """入场／了结时的上下文变量，供事后任意分组分析。无需重跑回测。

        两个来源：**期权链**给出合约自身的状态（IV、delta、价值状态），**信号层**
        给出标的当时的状态（均线、RSI、波动率）。后者不可省——"进场时 RSI 多少"
        是分组分析最常用的切法之一，事后补不回来。
        """

        def numeric(key: str) -> Maybe[float]:
            value = quote.get(key)
            if value is None or (isinstance(value, float) and np.isnan(value)):
                return Maybe.missing(f"{key} 不可用")
            return Maybe.some(float(value))

        spot = numeric("underlying_price")
        moneyness = (
            Maybe.some(float(spot.unwrap() / contract.strike - 1.0))
            if spot.is_present
            else Maybe.missing("标的价不可用")
        )
        context: dict[str, Maybe[float]] = {
            "underlying_price": spot,
            "iv": numeric("iv"),
            "delta": numeric("delta"),
            "gamma": numeric("gamma"),
            "vega": numeric("vega"),
            "theta": numeric("theta"),
            "moneyness": moneyness,
            "dte_calendar": Maybe.some(float((contract.expiry - day).days)),
            "dte_trading": Maybe.some(
                float(self.market.calendar.sessions_between(day, contract.expiry))
            ),
        }
        view = self.signals.on(day)
        for name in self.signals.names:
            context[f"sig_{name}"] = view[name]
        return context

    def _record(
        self, *, trade_id, contract, entry_day, exit_day, entry_price, exit_price,
        fees: FeeBreakdown, reason, suppressed, entry_quote, exit_quote, strategy, gaps, deferred
    ) -> TradeRecord:
        multiplier = self.config.contract.multiplier
        qty = strategy.quantity
        gross = (exit_price - entry_price) * multiplier * qty
        net = gross - fees.total
        if not np.isfinite(net):
            raise InvariantError(f"交易 {trade_id} 的盈亏不是有限数")
        return TradeRecord(
            trade_id=trade_id,
            engine=ENGINE_NAME,
            symbol=contract.symbol,
            entry_date=entry_day,
            exit_date=exit_day,
            contract=contract,
            quantity=qty,
            entry_price=entry_price,
            exit_price=exit_price,
            multiplier=multiplier,
            gross_pnl=gross,
            fees=fees.total,
            fee_commission=fees.commission,
            fee_regulatory=fees.regulatory,
            fee_slippage=fees.slippage,
            net_pnl=net,
            exit_reason=reason,
            suppressed_reasons=tuple(suppressed),
            holding_days=self.market.calendar.sessions_between(entry_day, exit_day),
            source=QuoteSource.SYNTHETIC
            if entry_quote.get("source") == QuoteSource.SYNTHETIC.value
            else QuoteSource.VENDOR,
            entry_context=self._context(entry_quote, entry_day, contract),
            exit_context=self._context(exit_quote, exit_day, contract),
            quote_gaps=gaps,
            deferred_days=deferred,
        )
