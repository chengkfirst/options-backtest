# 期权回测系统 · 阶段 1

帮你把七个策略组件的细节**定下来**的研究平台。全貌与后续阶段见 [`ROADMAP.md`](ROADMAP.md)。

本阶段交付**地基 + 成交表引擎**：能对 `setup/regime`、`entry`、`take profit`、`stop loss`
四个组件做参数扫描与分组研究。

## 安装

```bash
pip install -e ".[dev]"
```

## 跑一次回测

```python
from backtest import run

result = run(
    symbol="QQQ",
    start="2015-01-01", end="2024-12-31",
    entry="sma_20 > sma_50",                                   # 表达式或可调用对象
    select={"right": "call", "dte": 30, "delta": 0.30},        # 选约
    exit={"hold_days": 10, "take_profit": 1.0,                 # 四条件先达者了结
          "stop_loss": 0.5, "dte_floor": 5},
)

result.summary()              # 打印一页结论
result.trades                 # 交易明细 DataFrame
result.export("out/run1")     # 落盘：trades / pnl / events / manifest
```

可用信号：`close` `sma_20` `sma_50` `sma_200` `ema_20` `rsi_14` `atr_14` `mom_20`
`rv_20` `bb_width_20`。入场表达式只允许比较、`and/or/not` 与四则运算；名字打错在
**启动时**就报错。

## 怎么读结果

```python
result.stats()            # 笔数、胜率、盈亏比、平均持有
result.by_exit_reason()   # 按了结原因分组
result.distribution()     # 分位数、尾部、偏度峰度
result.costs()            # 佣金 / 监管费 / 滑点分项，扣费前后对照
result.concurrency        # 每日同时持仓数
result.events             # 运行事件：选约失败、不可成交、顺延、被动到期
result.manifest()         # 配置 + 数据来源 + 依赖版本（可复现的凭据）
```

单笔盈亏**分布**比均值重要得多。期权买方是低胜率、正偏、厚尾：胜率 30% 却赚钱，
或胜率 60% 却亏钱，都很正常。报告会在中位数与均值符号相反时显式警告——那意味着
平均收益由少数极值驱动，不可外推。

## 三条必须知道的边界

**一、合成期权链不能用来调期权侧参数。**

当前没有真实期权历史数据，链是用标的真实价格 + Black-Scholes 生成的。它没有波动率
偏斜、没有真实的 IV 动态、没有随流动性变化的价差。在它上面调止盈止损，调出来的是
"该合成模型下的最优解"，与真实市场**没有可靠关系**——不是"有偏差可修正"，是"无关"。

能研究的：`regime`、`entry` 的信号部分（基于标的真实价格）。
不能研究的：选约、止盈、止损、headwind、对冲。这些等真实数据接入（路线图阶段 6）。

**二、成交表引擎没有组合权益曲线。**

各笔交易独立计算，不考虑资金约束与并发上限。所以**没有最大回撤、没有夏普**——那些
需要权益曲线，要等事件驱动引擎（阶段 4）。它回答的是"这个想法有没有 edge"，不是
"这个策略能不能跑"。报告会给出峰值同时持仓数与所需资金，让资金缺口一眼可见。

**三、执行延迟一个交易日。**

T 日收盘产生意图，T+1 日收盘成交。入场如此，了结也如此。用 T 日收盘价同时做决策和
成交是最常见的前视来源，这里在架构上排除。副作用是：止损触发后次日可能反弹，因此
少量"止损"交易最终是盈利的——这是真实成本，不是 bug。

## 开发

```bash
pytest                              # 全部测试
python -m tools.check_deps          # 依赖方向检查
python -m tools.baseline check      # 回归基线比对
python -m tools.baseline update     # 显式更新基线（会打印前后差异）
```

三道防线各自拦不同的东西：

| 防线 | 拦什么 |
| --- | --- |
| 截断重算属性测试 | 前视偏差——把数据截到 t 重算，日期 t 的信号必须逐位一致 |
| 依赖方向检查 | 模块反向依赖；以及定价泄漏到合成链之外 |
| 回归基线 | 代码改动带来的非预期结果变化 |

## 目录

```
backtest/
  config.py      基准配置（design.md 的默认值）
  maybe.py       可缺失值——禁止裸 None / NaN / 0 表示缺失
  types.py       ContractId、TradeRecord、了结原因取值集合
  data/          行情（两套价格）、链、利率、分红、日历、缓存
    synthetic/   合成链与它内部的 Black-Scholes 工具
  signals/       指标、信号接口、无前视
  selection/     选约规则与筛选过程
  costs/         取价、费用、滑点、可成交性
  engine/        成交表引擎
  report/        交易统计、分布、成本分项
```

依赖严格单向：`report ← engine ← {selection, costs, signals} ← data`。
定价被关在 `data/synthetic/` 内部——上层消费链上的字段，不关心那些数字是市场观测
还是模型生成，这样接入真实数据时上层零改动。
