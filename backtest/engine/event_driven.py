"""事件驱动引擎：逐日推进。

和成交表引擎的区别只有一个，但很关键：**它知道任何时刻账上有多少钱、手里有几张合约**。
于是权益曲线、回撤、资金约束、并发上限、对冲——这些依赖组合状态的东西才成立。

两个引擎读**同一个** ``Strategy`` 定义。这不是为了省事，是为了能互相校验：
同一配置在无资金约束、无并发上限时，两边必须产出逐行相同的交易明细。任何一边算错
都会被对方照出来。

主循环每天的顺序是固定的，每一步的位置都有理由：

    ① 推进到 T 日
    ② 加载 T 日数据
    ③ 撮合 T-1 产生的意图  ← 在到期结算之前：到期日当天合约仍在交易，
                              T-1 下的平仓单应该有机会成交
    ④ T 日到期结算
    ⑤ 盯市 → 写入 T 日快照  ← 在策略回调之前：策略要看到今日市值；
                              且第 ⑥⑦ 步不改组合，所以这就是当日最终快照
    ⑥ 计算信号
    ⑦ 策略产出意图
    ⑧ 校验意图 → 入队等 T+1  ← 与产出分开：执行延迟造成的冲突必须在源头暴露
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from backtest.config import BacktestConfig
from backtest.costs.model import check_tradable, execute
from backtest.data.chain import ChainSource
from backtest.data.market import MarketData
from backtest.engine.plan import Context, Plan
from backtest.engine.review import Add, Close, PositionView, Reduce
from backtest.engine.strategy import ExitRule, Strategy
from backtest.errors import InvariantError, StartupError
from backtest.events import EventKind, EventLog
from backtest.engine.result import trades_frame
from backtest.portfolio.ledger import Ledger, Position, Snapshot
from backtest.risk.stress import aggregate_greeks
from backtest.selection.rules import select_contract
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

ENGINE_NAME = "event_driven"


#: 复查平仓时，``Close.kind`` 归到哪个了结原因。统计按这个分组，所以要一一对上。
REVIEW_EXIT_KIND = {
    "take_profit": ExitReason.TAKE_PROFIT,
    "stop_loss": ExitReason.STOP_LOSS,
    "headwind": ExitReason.RISK_FORCED,
    "signal": ExitReason.TIME_EXIT,
}


@dataclass(frozen=True, slots=True)
class Intent:
    """一笔交易意图。T 日产生，T+lag 日执行。"""

    contract: ContractId
    side: Side
    quantity: int
    created: date
    execute_on: date
    trade_id: int
    #: 开仓还是平仓。**必须是显式字段**：用给人看的说明文本当控制流开关，
    #: 改一下措辞就会把开仓当成平仓执行。
    kind: str = "entry"               # "entry" | "exit"
    reason: str = ""                  # 为什么下这一单，纯给人看
    exit_reasons: tuple[ExitReason, ...] = ()
    #: 计划式下带过来的东西：这一笔自己的出场规则、分组标签、备注。
    exit_rule: "ExitRule | None" = None
    tag: str = ""
    note: str = ""


@dataclass
class OpenTrade:
    """一笔尚未了结的逻辑交易。了结时变成 TradeRecord。"""

    trade_id: int
    contract: ContractId
    entry_date: date
    entry_price: float
    quantity: int
    side: Side                        # 建仓方向：BUY=多头，SELL=空头
    fees: float
    entry_quote: dict
    entry_signals: dict
    #: 持仓期内的最大有利/不利偏移，供 MFE/MAE 研究用
    mfe: float = 0.0
    mae: float = 0.0
    #: **这一笔自己的**出场规则。计划式下每笔可以不同——深回撤那档止损放宽、
    #: 持有更久。为空则回落到策略的全局规则。
    exit_rule: "ExitRule | None" = None
    #: 减仓时已经落袋的毛盈亏。**必须单独记**：最终记录按"剩余张数 × 进出价差"算，
    #: 天然漏掉先减掉的那几张。漏了不会报错，只会让这笔交易的盈亏静静地少一块。
    realised_gross: float = 0.0
    #: 下单时打的分组标签与备注，来自 Plan。一路存进交易明细，事后按它分类研究。
    tag: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class EventDrivenResult:
    records: tuple[TradeRecord, ...]
    snapshots: tuple[Snapshot, ...]
    config: BacktestConfig
    strategy: Strategy
    events: EventLog
    ledger: Ledger
    #: 每日组合层希腊字母。带默认值，所以必须排在无默认值的字段之后。
    greeks: tuple = ()
    #: **逐日持仓轨迹**：每个在持仓位每天的状态。⑤ headwind 研究的硬前置——
    #: 只有入场和出场两个点，看不出中间发生了什么。事后补不回来，所以先采着。
    paths: tuple = ()

    @property
    def paths_frame(self):
        """逐日持仓轨迹表。一行 = 某笔交易的某一天。"""
        import pandas as pd

        if not self.paths:
            return pd.DataFrame(columns=[
                "trade_id", "date", "days_held", "dte", "mark", "ratio",
                "mfe_so_far", "mae_so_far", "underlying", "iv", "delta", "tag"])
        return pd.DataFrame(list(self.paths))

    @property
    def trades(self) -> pd.DataFrame:
        """与成交表引擎同一种表——研究层不需要知道是哪条引擎跑的。"""
        return trades_frame(self.records)

    @property
    def greeks_frame(self) -> pd.DataFrame:
        """逐日组合层希腊字母。空仓日也在，值为 0——缺行和"当天没敞口"是两回事。"""
        if not self.greeks:
            return pd.DataFrame()
        return pd.DataFrame([g.as_dict() for g in self.greeks]).set_index("date")

    @property
    def concurrency(self) -> pd.Series:
        """每日同时持仓数。事件驱动是真的知道，不像成交表要事后推算。"""
        return pd.Series(
            {s.day: s.n_positions for s in self.snapshots}, name="concurrent_positions"
        )

    def capital_check(self) -> dict:
        """资金是否够用。事件驱动已经强制了约束，所以这里是事后确认。"""
        peak = max((s.n_positions for s in self.snapshots), default=0)
        peak_deployed = max((s.deployed for s in self.snapshots), default=0.0)
        return {
            "peak_positions": peak, "peak_capital": peak_deployed,
            "notional_capital": self.config.notional_capital,
            "feasible": peak_deployed <= self.config.notional_capital,
        }

    @property
    def equity(self) -> pd.DataFrame:
        """两条权益曲线 + 资金占用。"""
        return pd.DataFrame(
            [
                {
                    "date": s.day, "cash": s.cash,
                    "equity_mid": s.equity_mid, "equity_liq": s.equity_liq,
                    "option_value": s.option_value_mid, "stock_value": s.stock_value,
                    "positions": s.n_positions, "deployed": s.deployed,
                }
                for s in self.snapshots
            ]
        ).set_index("date")


class EventDrivenEngine:
    """逐日推进的回测引擎。"""

    def __init__(
        self,
        market: MarketData,
        chain: ChainSource,
        signals: SignalSet,
        config: BacktestConfig | None = None,
        events: EventLog | None = None,
        *,
        max_concurrent: int | None = None,
        enforce_capital: bool = True,
        limits=None,
        brake=None,
    ) -> None:
        self.market = market
        self.chain = chain
        self.signals = signals
        self.config = config or market.config
        self.events = events or market.events
        #: None 表示不限。设为 None 且 enforce_capital=False 时，本引擎应与成交表引擎一致。
        self.max_concurrent = max_concurrent
        self.enforce_capital = enforce_capital
        #: 组合层敞口上限（ExposureLimits）与回撤熔断（DrawdownBrake）。
        #: 两者都只拦新开仓，不主动减仓——减仓是 headwind 的事，那还没做。
        self.limits = limits
        self.brake = brake

    # ── 守卫 ──────────────────────────────────────────────
    def _guard(self, strategy: Strategy) -> None:
        self.signals.require(list(strategy.entry.requires))
        if strategy.exit.hold_days is None and strategy.exit.dte_floor is None:
            raise StartupError("缺少时间类了结条件，仓位会被拖到到期")
        lag = self.config.execution_lag_days
        floor = strategy.exit.dte_floor
        if floor is not None and floor < lag + 1:
            raise StartupError(
                f"剩余到期下限 {floor} 过小：执行延迟 {lag} 天，下限至少 {lag + 1}"
            )
        # 选约写成函数时，方向由函数自己决定，这里查不了也不该查。
        if not callable(strategy.select) and strategy.select.right is Right.CALL \
                and getattr(strategy, "short", False):
            raise StartupError("裸卖 call 未被支持：保证金模型还没做，给出的数字会是错的")
        if strategy.allow_stacking:
            # 真实账户按合约净额记仓：同一张合约买两次，在券商那里就是一个仓位，
            # 不是两笔独立持仓。账本忠实地这么做了，但引擎按 trade_id 追踪逻辑交易，
            # 于是第二笔永远等不到"它的"持仓被平，会被静默丢掉。
            # 要支持得加一层批次追踪，现在没做——所以拒绝，而不是丢。
            raise StartupError(
                "事件驱动引擎暂不支持 allow_stacking：真实账户按合约净额记仓，"
                "同一张合约的多笔建仓会被合并成一个持仓，无法再拆回成多笔逻辑交易。"
                "需要批次追踪层，尚未实现。成交表引擎可以开这个开关，"
                "但那样两个引擎的结果不再可比。"
            )

    # ── 主循环 ────────────────────────────────────────────
    def run(self, strategy: Strategy) -> EventDrivenResult:
        self._guard(strategy)
        cal = self.market.calendar
        ledger = Ledger(self.config.notional_capital, self.config.contract)
        queue: list[Intent] = []
        open_trades: dict[int, OpenTrade] = {}
        records: list[TradeRecord] = []
        snapshots: list[Snapshot] = []
        greeks_daily: list = []
        next_trade_id = 1
        self._last_equity = float(self.config.notional_capital)
        self._peak_equity = float(self.config.notional_capital)
        self._drawdown = 0.0
        paths: list[dict] = []
        self._brake_state = "normal"
        self._last_greeks = None
        self._closed_pnl = records          # Kelly 用它估参数，引用同一个列表

        for day in self.market.sessions():
            # ③ 撮合昨日意图
            due, queue = [i for i in queue if i.execute_on == day], [i for i in queue if i.execute_on > day]
            for intent in due:
                closed = self._execute(intent, ledger, open_trades, day)
                if closed is not None:
                    records.append(closed)

            # ④ 到期结算
            for pos in list(ledger.positions):
                if pos.contract.expiry <= day:
                    rec = self._settle(pos, ledger, open_trades, day)
                    if rec is not None:
                        records.append(rec)

            # ⑤ 盯市
            quotes = self._quotes(day, ledger.positions)
            snap = ledger.snapshot(day, quotes, self.market.spot(day))
            ledger.check_invariant(snap)
            snapshots.append(snap)
            self._last_equity = snap.equity_mid
            self._peak_equity = max(self._peak_equity, snap.equity_mid)
            # 回撤无条件算一次。以前只在熔断分支里算，于是计划函数想看当前回撤
            # 时拿不到——而"回撤多深"恰恰是仓位和减仓判断最常用的输入。
            self._drawdown = snap.equity_mid / self._peak_equity - 1.0
            g = aggregate_greeks(ledger.positions, self.chain, day,
                                 self.market.spot(day).or_else(float("nan")), snap.equity_mid)
            greeks_daily.append(g)
            self._last_greeks = g
            if self.brake is not None:
                new_state = self.brake.state(self._drawdown)
                if new_state != self._brake_state:
                    self.events.record(
                        EventKind.CANDIDATE_DROPPED, day,
                        f"回撤熔断：{self._brake_state} → {new_state}（回撤 {self._drawdown:.1%}）")
                    self._brake_state = new_state
            self._track_excursions(ledger, open_trades, quotes, day, paths)

            # ⑥⑦ 信号 → 策略
            view = self.signals.on(day)
            intents: list[Intent] = []

            for pos in ledger.positions:
                trade = open_trades.get(pos.trade_id)
                if trade is None or self._already_queued(queue, pos.contract):
                    continue
                # 静态规则先判。它是**安全网**：时间类了结不允许被复查绕过，
                # 否则一个写错的复查函数会把仓位悄悄拖到到期。
                reasons = self._exit_reasons(pos, trade, quotes, day, strategy)
                action = None
                if not reasons and strategy.review is not None:
                    action = self._review_action(
                        strategy, self._market_ctx(day, ledger), pos, trade, quotes, day)

                target = cal.shift(day, self.config.execution_lag_days)
                if target is None or target > self.config.end:
                    continue               # 区间末尾：留给到期结算处理

                if reasons or isinstance(action, Close):
                    if isinstance(action, Close) and not reasons:
                        reasons = [REVIEW_EXIT_KIND[action.kind]]
                        self.events.record(EventKind.CANDIDATE_DROPPED, day,
                                           f"复查平仓：{action.reason}", str(pos.contract))
                    intents.append(Intent(
                        contract=pos.contract,
                        side=Side.SELL if pos.quantity > 0 else Side.BUY,
                        quantity=pos.abs_quantity, created=day, execute_on=target,
                        trade_id=pos.trade_id, kind="exit", reason="exit",
                        exit_reasons=tuple(reasons),
                    ))
                elif isinstance(action, Reduce):
                    # 向下取整，且至少留 1 张——减到 0 张应当用 Close 表达，
                    # 让"减仓"悄悄变成"平仓"会让统计分不清这两件事。
                    cut = int(pos.abs_quantity * action.fraction)
                    if cut < 1 or cut >= pos.abs_quantity:
                        self.events.record(
                            EventKind.CANDIDATE_DROPPED, day,
                            f"减仓 {action.fraction:.0%} 算出 {cut} 张（当前 {pos.abs_quantity} 张），跳过",
                            str(pos.contract))
                    else:
                        intents.append(Intent(
                            contract=pos.contract,
                            side=Side.SELL if pos.quantity > 0 else Side.BUY,
                            quantity=cut, created=day, execute_on=target,
                            trade_id=pos.trade_id, kind="reduce",
                            reason=action.reason or "复查减仓",
                        ))
                elif isinstance(action, Add):
                    intents.append(Intent(
                        contract=pos.contract,
                        side=Side.BUY if pos.quantity > 0 else Side.SELL,
                        quantity=action.contracts, created=day, execute_on=target,
                        trade_id=pos.trade_id, kind="add",
                        reason=action.reason or "复查加仓",
                    ))

            if strategy.entry.condition(view):
                intent = self._entry_intent(day, strategy, ledger, queue, next_trade_id)
                if intent is not None:
                    intents.append(intent)
                    next_trade_id += 1

            # ⑧ 校验入队
            for intent in intents:
                if self._validate(intent, ledger, day):
                    queue.append(intent)

        return EventDrivenResult(
            paths=tuple(paths),
            records=tuple(records), snapshots=tuple(snapshots), greeks=tuple(greeks_daily),
            config=self.config, strategy=strategy, events=self.events, ledger=ledger,
        )

    # ── 各步 ──────────────────────────────────────────────
    def _quotes(self, day: date, positions) -> dict[ContractId, tuple]:
        out: dict[ContractId, tuple] = {}
        for pos in positions:
            frame = self.chain.series(pos.contract, [day])
            if frame.empty:
                self.events.record(EventKind.QUOTE_MISSING, day, "盯市无报价", str(pos.contract))
                continue
            row = frame.iloc[0]
            # 带上整行：轨迹采集要 iv 与 delta。只回 (bid, ask) 的话，
            # 想多记一个字段就得再查一次链——那是白跑一遍。
            out[pos.contract] = (float(row["bid"]), float(row["ask"]), row)
        return out

    def _track_excursions(self, ledger, open_trades, quotes, day, paths) -> None:
        """逐日采集每个在持仓位的状态。

        两个用途：

        1. **MFE / MAE**——止盈该设在哪、止损该设在哪，看分布比扫参数直接得多。
        2. **逐日轨迹**——这是 ⑤ headwind 研究的**硬前置**。只有入场和出场两个点，
           看不出中间发生了什么，也就无法回答"结果不好的持仓事前有没有预警"。

        数据只能在持仓过程中采，**事后补不回来**——这就是为什么即使 headwind 的
        研究工具还没写，采集也得先做。
        """
        for pos in ledger.positions:
            trade = open_trades.get(pos.trade_id)
            quote = quotes.get(pos.contract)
            if trade is None or quote is None:
                continue
            mark = (quote[0] + quote[1]) / 2.0
            ratio = (mark / trade.entry_price - 1.0) * (1 if pos.quantity > 0 else -1)
            trade.mfe = max(trade.mfe, ratio)
            trade.mae = min(trade.mae, ratio)

            row = quote[2] if len(quote) > 2 else None
            paths.append({
                "trade_id": trade.trade_id,
                "date": day,
                "days_held": self.market.calendar.sessions_between(trade.entry_date, day),
                "dte": self.market.calendar.sessions_between(day, pos.contract.expiry),
                "mark": mark,
                "ratio": ratio,              # 相对入场价的盈亏比例
                "mfe_so_far": trade.mfe,     # 至今的最好/最差，**不是事后回看的**
                "mae_so_far": trade.mae,
                "underlying": self.market.spot(day).or_else(float("nan")),
                "iv": float(row["iv"]) if row is not None and "iv" in row else float("nan"),
                "delta": float(row["delta"]) if row is not None and "delta" in row else float("nan"),
                "tag": trade.tag,
            })

    def _exit_reasons(self, pos: Position, trade: OpenTrade, quotes, day: date, strategy: Strategy):
        # 这一笔自己的规则优先。计划式下每笔的止盈止损可以不同——那正是
        # "七个组件互相联动"的落点：同一个判断同时决定了买什么和怎么了结。
        rule = trade.exit_rule or strategy.exit
        cal = self.market.calendar
        reasons: list[ExitReason] = []
        quote = quotes.get(pos.contract)
        if quote is not None:
            exit_price = quote[0] if pos.quantity > 0 else quote[1]
            ratio = (exit_price / trade.entry_price - 1.0) * (1 if pos.quantity > 0 else -1)
            if rule.take_profit is not None and ratio >= rule.take_profit:
                reasons.append(ExitReason.TAKE_PROFIT)
            if rule.stop_loss is not None and ratio <= -rule.stop_loss:
                reasons.append(ExitReason.STOP_LOSS)
        if rule.dte_floor is not None and cal.sessions_between(day, pos.contract.expiry) <= rule.dte_floor:
            reasons.append(ExitReason.DTE_FLOOR)
        if rule.hold_days is not None and cal.sessions_between(trade.entry_date, day) >= rule.hold_days:
            reasons.append(ExitReason.TIME_EXIT)
        return reasons

    def _entry_intent(self, day, strategy, ledger, queue, trade_id) -> Intent | None:
        if self.max_concurrent is not None and len(ledger) >= self.max_concurrent:
            self.events.record(EventKind.CANDIDATE_DROPPED, day, f"并发已达上限 {self.max_concurrent}")
            return None
        if self.brake is not None and self._brake_state == "halted":
            self.events.record(EventKind.CANDIDATE_DROPPED, day, "回撤熔断中，停止开新仓")
            return None
        if self.limits is not None and self._last_greeks is not None:
            allowed, why = self.limits.check(self._last_greeks, self._last_equity)
            if not allowed:
                self.events.record(EventKind.CANDIDATE_DROPPED, day, f"敞口限额：{why}")
                return None
        target = self.market.calendar.shift(day, self.config.execution_lag_days)
        if target is None or target > self.config.end:
            self.events.record(EventKind.CANDIDATE_DROPPED, day, "执行日超出回测区间")
            return None

        if strategy.plan is not None:
            return self._plan_intent(day, target, strategy, ledger, queue, trade_id)

        selection = select_contract(
            self.chain.snapshot(target), strategy.select, self.config, self.market.calendar, target,
            # 同上：链是执行日 target 的，视图是决策日 day 的。
            self.signals.on(day),
        )
        if not selection.ok:
            self.events.record(EventKind.SELECTION_FAILED, target, selection.describe())
            return None
        # 选中了，但有被跳过的约束或退化的排序——这些不该只留在 trace 里，
        # 使用者看不到就等于没提示。
        for why in selection.skipped_filters:
            self.events.record(EventKind.CANDIDATE_DROPPED, target, f"选约提示：{why}")
        contract = selection.contract.unwrap()
        if not strategy.allow_stacking and (
            ledger.position(contract) is not None or self._already_queued(queue, contract)
        ):
            self.events.record(
                EventKind.CANDIDATE_DROPPED, day,
                "同一合约已在持仓中，allow_stacking 为假", str(contract))
            return None
        quantity = self._size(strategy, ledger, contract, day, target)
        if self.brake is not None and self._brake_state == "reduced":
            quantity = int(quantity * self.brake.scale(
                self._last_equity / self._peak_equity - 1.0))
        if quantity <= 0:
            self.events.record(
                EventKind.CANDIDATE_DROPPED, day,
                f"仓位算出 {quantity} 张（方法 {strategy.sizing.method}）——不足一张即不开仓，"
                f"绝不向上取整", str(contract))
            return None
        return Intent(contract=contract, side=Side.BUY, quantity=quantity,
                      created=day, execute_on=target, trade_id=trade_id,
                      kind="entry", reason="entry")

    def _market_ctx(self, day, ledger) -> Context:
        """复查时的市场与组合上下文。

        **不带候选链**：复查看的是已有持仓，不是在挑新合约。传一张空表而不是
        当天的全链，是为了不给人"可以在复查里换合约"的错觉——那是展期，
        要另外支持（见 review.Roll）。
        """
        import pandas as pd

        return Context(
            day=day, chain=pd.DataFrame(), spot=self.market.spot(day).or_else(float("nan")),
            equity=self._last_equity, cash=ledger.cash, positions=len(ledger),
            drawdown=self._drawdown, _view=self.signals.on(day),
        )

    def _review_action(self, strategy, ctx, pos, trade, quotes, day):
        """跑用户的复查函数。异常不吞，带上是哪天哪个合约一起抛。"""
        quote = quotes.get(pos.contract)
        if quote is None:
            return None                     # 今天没报价，无从判断，留给静态规则
        row = quote[2] if len(quote) > 2 else None
        mark = (quote[0] + quote[1]) / 2.0
        view = PositionView(
            contract=pos.contract, entry_date=trade.entry_date,
            entry_price=trade.entry_price, quantity=pos.abs_quantity,
            days_held=self.market.calendar.sessions_between(trade.entry_date, day),
            dte=self.market.calendar.sessions_between(day, pos.contract.expiry),
            mark=mark,
            ratio=(mark / trade.entry_price - 1.0) * (1 if pos.quantity > 0 else -1),
            mfe_so_far=trade.mfe, mae_so_far=trade.mae,
            iv=float(row["iv"]) if row is not None and "iv" in row else float("nan"),
            delta=float(row["delta"]) if row is not None and "delta" in row else float("nan"),
            tag=trade.tag,
        )
        try:
            action = strategy.review(ctx, view)
        except Exception as exc:  # noqa: BLE001
            raise StartupError(
                f"复查函数在 {day} 处理 {pos.contract} 时抛出异常："
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if action is not None and not isinstance(action, (Close, Reduce, Add)):
            raise StartupError(
                f"复查函数在 {day} 返回了 {type(action).__name__}，"
                "应当返回 Close / Reduce / Add 之一，或 None 表示继续持有。"
            )
        return action

    def _plan_intent(self, day, target, strategy, ledger, queue, trade_id) -> Intent | None:
        """计划式入场：一个函数同时决定买什么、买多少、怎么了结。

        七个组件在这里是联动的——同一个判断产出合约、仓位、止盈止损。这正是
        参数化写法表达不了的东西。
        """
        from backtest.selection.rules import enrich_chain

        chain = enrich_chain(
            self.chain.snapshot(target), self.config, self.market.calendar, target
        )
        if chain.empty:
            self.events.record(EventKind.SELECTION_FAILED, target, "当日没有可执行的合约")
            return None

        ctx = Context(
            day=day, chain=chain, spot=self.market.spot(day),
            equity=self._last_equity, cash=ledger.cash, positions=len(ledger),
            drawdown=self._drawdown, _view=self.signals.on(day),
        )
        try:
            plan = strategy.plan(ctx)
        except Exception as exc:  # noqa: BLE001
            # 不吞。计划函数里的错必须带着哪一天一起抛出来。
            raise StartupError(
                f"计划函数在 {day} 抛出异常：{type(exc).__name__}: {exc}"
            ) from exc

        if plan is None:
            return None
        if not isinstance(plan, Plan):
            raise StartupError(
                f"计划函数在 {day} 返回了 {type(plan).__name__}，应当返回 Plan 或 None。"
            )

        row = plan.contract
        contract = ContractId(
            symbol=str(row["symbol"]),
            expiry=row["expiry"] if isinstance(row["expiry"], date)
            else pd.Timestamp(row["expiry"]).date(),
            right=Right(row["right"]),
            strike=float(row["strike"]),
        )
        # 用户可能从别处拼了一行，或者筛之前就把链改了。校验它确实在当日可执行的链上。
        key = chain[(chain["symbol"].astype(str) == contract.symbol)
                    & (chain["strike"].astype(float) == contract.strike)
                    & (chain["right"].astype(str) == contract.right.value)]
        if key.empty:
            raise StartupError(
                f"计划在 {day} 选的合约不在当日可执行的链上：{contract}。"
                "请从 ctx.chain 里挑一行，不要自己拼。"
            )

        if not strategy.allow_stacking and (
            ledger.position(contract) is not None or self._already_queued(queue, contract)
        ):
            self.events.record(EventKind.CANDIDATE_DROPPED, day,
                               "同一合约已在持仓中，allow_stacking 为假", str(contract))
            return None

        quantity = self._plan_size(plan, contract, target, strategy)
        if quantity <= 0:
            self.events.record(EventKind.CANDIDATE_DROPPED, day,
                               f"计划算出 0 张（权益 {ctx.equity:,.0f}）", plan.tag)
            return None
        if self.brake is not None and self._brake_state == "reduced":
            quantity = int(quantity * self.brake.scale(self._drawdown))
            if quantity <= 0:
                self.events.record(EventKind.CANDIDATE_DROPPED, day, "回撤熔断降规模后为 0 张")
                return None

        return Intent(
            contract=contract, side=Side.BUY, quantity=quantity, created=day,
            execute_on=target, trade_id=trade_id,
            kind="entry", reason=plan.note or plan.tag or "计划入场",
            exit_rule=self._plan_exit_rule(plan, strategy),
            tag=plan.tag, note=plan.note,
        )

    def _plan_exit_rule(self, plan: Plan, strategy: Strategy) -> ExitRule:
        """计划没给的了结项回落到策略的全局规则，不是回落到"无限制"。

        回落到无限制会让"计划里忘了写 dte_floor"变成"这一笔被拖到到期"，
        而且不报错。回落到全局值至少还有那道时间类了结的守卫。
        """
        base = strategy.exit
        return ExitRule(
            hold_days=plan.max_hold if plan.max_hold is not None else base.hold_days,
            take_profit=plan.take_profit if plan.take_profit is not None else base.take_profit,
            stop_loss=plan.stop_loss if plan.stop_loss is not None else base.stop_loss,
            dte_floor=plan.dte_floor if plan.dte_floor is not None else base.dte_floor,
        )

    def _plan_size(self, plan: Plan, contract, execute_on, strategy) -> int:
        """按计划里给的仓位口径算张数。三个都没给就退回策略的仓位方法。

        算出 0 张就是不开仓——**绝不向上取整到 1**，那会让"风险占 1.5%"
        悄悄变成"风险占 6%"。
        """
        if plan.contracts is not None:
            return plan.contracts
        if plan.risk_pct is None and plan.notional is None:
            return self._size(strategy, None, contract, execute_on, execute_on)

        frame = self.chain.series(contract, [execute_on])
        if frame.empty:
            return 0
        premium = float(frame.iloc[0]["ask"]) * self.config.contract.multiplier
        if premium <= 0:
            return 0
        budget = (plan.risk_pct * self._last_equity) if plan.risk_pct is not None else plan.notional
        return int(budget // premium)

    def _size(self, strategy, ledger, contract, day, execute_on) -> int:
        """按配置的仓位方法算张数。

        对买方，最大损失就是权利金，所以"每笔风险"可以直接除。任何方法算出 0 张都
        不开仓——**绝不向上取整到 1**，那会让"风险占 2%"悄悄变成"风险占 8%"。
        """
        sizing = strategy.sizing
        if sizing.method == "fixed":
            return min(sizing.contracts, sizing.max_contracts)

        frame = self.chain.series(contract, [execute_on])
        if frame.empty:
            return 0
        premium = float(frame.iloc[0]["ask"]) * self.config.contract.multiplier
        if premium <= 0:
            return 0
        equity = self._last_equity

        if sizing.method == "risk_pct":
            # **规模 = 风险预算 ÷ 止损距离。** 止损设在 -50% 时，单笔真实风险只有
            # 权利金的一半，按全损算会把仓位压小一倍。没有止损才等于全损。
            budget = equity * sizing.risk_pct / self._loss_fraction(strategy)
        elif sizing.method == "fixed_notional":
            budget = sizing.notional
        elif sizing.method == "scale_by_vol":
            vol = self.market.volatility(day)
            if vol.is_missing:
                self.events.record(EventKind.CANDIDATE_DROPPED, day,
                                   f"scale_by_vol 需要波动率，当日不可得：{vol.reason}")
                return 0
            # 标的波动高就少买。**这不是组合波动率目标**——那需要预测组合波动，
            # 期权上得靠希腊字母推。名字如实反映它做的事。
            budget = (equity * sizing.risk_pct / self._loss_fraction(strategy)
                      * (sizing.target_vol / max(vol.unwrap(), 1e-6)))
        else:
            raise StartupError(f"未实现的仓位方法 {sizing.method}")

        return min(int(budget // premium), sizing.max_contracts)

    def _loss_fraction(self, strategy) -> float:
        """单笔最坏情况亏掉权利金的几分之几。

        止损 0.5 表示亏到 -50% 就走，所以真实风险是权利金的一半。没设止损时
        才等于全损（买方最大损失就是权利金）。

        这是「规模 = 风险预算 ÷ 止损距离」这条约束在代码里的落点——两者分开设，
        算出来的仓位就和你以为的风险对不上。
        """
        stop = strategy.exit.stop_loss
        if stop is None or not 0 < stop <= 1:
            return 1.0
        return float(stop)

    @staticmethod
    def _already_queued(queue, contract) -> bool:
        return any(i.contract == contract for i in queue)

    def _validate(self, intent: Intent, ledger: Ledger, day: date) -> bool:
        """执行延迟造成的冲突必须在源头暴露，不能等到执行时才失败。"""
        if intent.contract.expiry <= intent.execute_on:
            self.events.record(
                EventKind.CANDIDATE_DROPPED, day,
                f"{intent.contract} 在执行日 {intent.execute_on} 已到期", str(intent.contract))
            return False
        if intent.reason == "entry" and self.enforce_capital:
            frame = self.chain.series(intent.contract, [intent.execute_on])
            if not frame.empty:
                need = float(frame.iloc[0]["ask"]) * intent.quantity * self.config.contract.multiplier
                if need > ledger.cash:
                    self.events.record(
                        EventKind.NOT_TRADABLE, day,
                        f"资金不足：需要 {need:,.0f}，可用 {ledger.cash:,.0f}", str(intent.contract))
                    return False
        return True

    def _execute(self, intent: Intent, ledger: Ledger, open_trades, day: date) -> TradeRecord | None:
        frame = self.chain.series(intent.contract, [day])
        if frame.empty:
            self.events.record(EventKind.NOT_TRADABLE, day, "执行日无报价", str(intent.contract))
            return None
        row = frame.iloc[0]
        tradable = check_tradable(row["bid"], row["ask"], self.config.costs)
        if not tradable.ok:
            self.events.record(EventKind.NOT_TRADABLE, day, tradable.reason or "", str(intent.contract))
            return None

        price, fees = execute(
            bid=row["bid"], ask=row["ask"], side=intent.side, quantity=intent.quantity,
            multiplier=self.config.contract.multiplier, costs=self.config.costs,
        )
        ledger.apply_fill(contract=intent.contract, side=intent.side, quantity=intent.quantity,
                          price=price, fees=fees, when=day, trade_id=intent.trade_id)

        if intent.kind == "entry":
            open_trades[intent.trade_id] = OpenTrade(
                trade_id=intent.trade_id, contract=intent.contract, entry_date=day,
                entry_price=price, quantity=intent.quantity, side=intent.side,
                fees=fees.total, entry_quote=row.to_dict(),
                entry_signals={n: self.signals.on(day)[n] for n in self.signals.names},
                exit_rule=intent.exit_rule, tag=intent.tag, note=intent.note,
            )
            return None

        if intent.kind in ("reduce", "add"):
            # **不产生交易记录，也不弹出 OpenTrade。** 减仓/加仓之后仍是同一笔
            # 逻辑交易——按持仓统计会让一笔变成好几笔，胜率与持有期全部失真。
            trade = open_trades.get(intent.trade_id)
            if trade is None:
                raise InvariantError(f"{intent.kind} 意图 {intent.trade_id} 找不到对应的持仓记录")
            if intent.kind == "add":
                total = trade.entry_price * trade.quantity + price * intent.quantity
                trade.quantity += intent.quantity
                trade.entry_price = total / trade.quantity   # 加权平均成本
            else:
                direction = 1 if trade.side is Side.BUY else -1
                trade.realised_gross += (
                    (price - trade.entry_price) * self.config.contract.multiplier
                    * intent.quantity * direction
                )
                trade.quantity -= intent.quantity
            trade.fees += fees.total
            self.events.record(
                EventKind.CANDIDATE_DROPPED, day,
                f"{'加仓' if intent.kind == 'add' else '减仓'} {intent.quantity} 张："
                f"{intent.reason}（现 {trade.quantity} 张）", str(intent.contract))
            return None

        trade = open_trades.pop(intent.trade_id, None)
        if trade is None:
            raise InvariantError(f"了结意图 {intent.trade_id} 找不到对应的持仓记录")
        reason = resolve_exit_reason(list(intent.exit_reasons)) if intent.exit_reasons else ExitReason.TIME_EXIT
        return self._record(trade, exit_day=day, exit_price=price, exit_fees=fees.total,
                            exit_quote=row.to_dict(), reason=reason,
                            suppressed=tuple(r for r in intent.exit_reasons if r is not reason))

    def _settle(self, pos: Position, ledger: Ledger, open_trades, day: date) -> TradeRecord | None:
        spot = self.market.spot(day)
        kind = ledger.settle_expiry(pos.contract, spot, day)
        trade = open_trades.pop(pos.trade_id, None)
        if trade is None:
            return None
        s = spot.unwrap()
        intrinsic = (max(s - pos.contract.strike, 0.0) if pos.contract.right is Right.CALL
                     else max(pos.contract.strike - s, 0.0))
        reason = ExitReason.EXPIRY_OTM if kind == "worthless" else ExitReason.EXPIRY_ITM
        self.events.record(EventKind.PASSIVE_EXPIRY, day,
                           f"被动到期（{kind}），内在价值 {intrinsic:.2f}", str(pos.contract))
        quote = dict(trade.entry_quote)
        quote.update({"asof": day, "underlying_price": s, "bid": intrinsic, "ask": intrinsic})
        return self._record(trade, exit_day=day, exit_price=intrinsic, exit_fees=0.0,
                            exit_quote=quote, reason=reason, suppressed=())

    def _record(self, trade: OpenTrade, *, exit_day, exit_price, exit_fees, exit_quote, reason, suppressed):
        mult = self.config.contract.multiplier
        direction = 1 if trade.side is Side.BUY else -1
        # 剩余张数的盈亏 + 减仓时已经落袋的。少了后一项，减过仓的交易盈亏会偏小，
        # 而且不报任何错——是这一类改动里最容易漏、最难发现的地方。
        gross = ((exit_price - trade.entry_price) * mult * trade.quantity * direction
                 + trade.realised_gross)
        fees = trade.fees + exit_fees
        ctx_in = self._context(trade.entry_quote, trade.entry_date, trade.contract, trade.entry_signals)
        ctx_out = self._context(exit_quote, exit_day, trade.contract,
                                {n: self.signals.on(exit_day)[n] for n in self.signals.names})
        return TradeRecord(
            trade_id=trade.trade_id, engine=ENGINE_NAME, symbol=trade.contract.symbol,
            entry_date=trade.entry_date, exit_date=exit_day, contract=trade.contract,
            quantity=trade.quantity, entry_price=trade.entry_price, exit_price=exit_price,
            multiplier=mult, gross_pnl=gross, fees=fees,
            fee_commission=fees, fee_regulatory=0.0, fee_slippage=0.0,
            net_pnl=gross - fees, exit_reason=reason, suppressed_reasons=suppressed,
            holding_days=self.market.calendar.sessions_between(trade.entry_date, exit_day),
            source=QuoteSource.SYNTHETIC
            if trade.entry_quote.get("source") == QuoteSource.SYNTHETIC.value else QuoteSource.VENDOR,
            entry_context=ctx_in, exit_context=ctx_out,
            mfe=trade.mfe, mae=trade.mae,
            tag=trade.tag, note=trade.note,
        )

    def _context(self, quote: dict, day: date, contract: ContractId, signals: dict):
        import numpy as np

        from backtest.maybe import Maybe

        def numeric(key):
            v = quote.get(key)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                return Maybe.missing(f"{key} 不可用")
            return Maybe.some(float(v))

        spot = numeric("underlying_price")
        ctx = {
            "underlying_price": spot, "iv": numeric("iv"), "delta": numeric("delta"),
            "gamma": numeric("gamma"), "vega": numeric("vega"), "theta": numeric("theta"),
            "moneyness": Maybe.some(float(spot.unwrap() / contract.strike - 1.0))
            if spot.is_present else Maybe.missing("标的价不可用"),
            "dte_calendar": Maybe.some(float((contract.expiry - day).days)),
            "dte_trading": Maybe.some(float(self.market.calendar.sessions_between(day, contract.expiry))),
        }
        for name, value in signals.items():
            ctx[f"sig_{name}"] = value
        return ctx
