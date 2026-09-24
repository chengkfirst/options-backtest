# 期权回测平台

研究美股期权策略（QQQ / SPY，中频，几天到几个月）的工具。两个用途：

1. **把策略细节定下来** —— 七个组件各自该怎么设
2. **定型之后长期验证它** —— 优化、证伪、多方验证

> 想知道「现在做到哪了、还差什么」→ **[需求.md](需求.md)**（54 条验收标准，逐条标了状态）
> 想知道「系统长什么样、东西怎么流动」→ **[ROADMAP.md](ROADMAP.md)**

---

## 安装

需要 Python 3.11+。

```bash
pip install -e ".[dev]"
```

## 用法一：网页（推荐从这里开始）

```bash
python app/server.py
```

打开 http://localhost:8765。只监听本机，不对外。

左边栏按**交易里发生的顺序**分四层，不是七个平等的参数框：

| 层 | 管什么 |
|---|---|
| **决定之前** | ① regime —— 今天该不该做、用哪一档 |
| **一笔交易的决定** | ② 时点 · ②b 选什么 · ③④ 了结点 · ⑥ 多大 |
| **持仓期间** | ⑤ headwind —— 论据部分失效时怎么办 |
| **组合层** | ⑦ hedge —— 不想承担的风险怎么剥离 |

## 用法二：Python / Jupyter

```python
from backtest import run

r = run(symbol="QQQ", start="2020-01-01", end="2024-12-31",
        entry="close < sma_20 * 0.98",
        exit={"hold_days": 20, "take_profit": 0.8, "stop_loss": 0.4, "dte_floor": 7},
        engine="event_driven",
        sizing={"method": "risk_pct", "risk_pct": 0.02})

r.summary()          # 一页结论
r.trades             # 交易明细（含每笔的 greeks、IV、以及入场/出场时刻的全部信号）
r.equity_metrics()   # 收益、回撤、夏普、资金占用
```

---

# 交易计划：一个函数定完一笔

策略可以逐个组件填参数，也可以写成一个函数。后者存在的理由是组件之间有约束，
最硬的一条：

```
仓位规模 = 风险预算 ÷ 止损距离
```

三个量定了两个，第三个就确定了。参数模式把止损和规模做成两个独立输入，
两者会互相矛盾而系统不会报错。


```python
from backtest import run
from backtest.engine.plan import Plan

def plan(ctx):
    rsi = ctx.signal("rsi_14", default=50.0)
    if rsi > 45:
        return None                              # ② 时点：不够超卖，今天不做

    deep = rsi < 35
    c = ctx.calls[(ctx.calls.spread < 0.10) & ctx.calls.delta.notna()]
    c = c[c.dte.between(45, 75)] if deep else c[c.dte.between(21, 45)]
    if c.empty:
        return None

    return Plan(
        contract    = c.iloc[(c.delta - (0.50 if deep else 0.30)).abs().argmin()],  # ②b
        risk_pct    = 0.030 if deep else 0.015,   # ⑥ 多大
        stop_loss   = 0.60  if deep else 0.35,    # ④ 论据失效点
        take_profit = 1.00  if deep else 0.60,    # ③ 兑现点
        max_hold    = 40    if deep else 15,
        tag         = "深超卖" if deep else "浅超卖",
    )

r = run(symbol="QQQ", start="2022-01-01", end="2023-12-31",
        plan=plan, exit={"hold_days": 20, "dte_floor": 7}, engine="event_driven")

r.trades.groupby("tag").agg(笔数=("net_pnl","size"), 平均张数=("quantity","mean"))
```

`tag` 会存进每笔明细。**分类口径来自你下单时的真实判断**，不是事后按结果硬分的——
后者会把结论分析成自己想要的样子。

### `ctx` 里有什么

```
ctx.chain / ctx.calls / ctx.puts   当日可选合约（DataFrame，一行一张）
ctx.equity  ctx.cash  ctx.positions  ctx.drawdown    组合状态
ctx.spot  ctx.day
ctx.signal("名字", default=...)    取信号；缺失且没给默认值会**报错**，不会悄悄给 0
```

链上每行有：`iv delta gamma vega theta` · `bid ask mid spread` · `dte`（日历日）·
`sessions_left`（交易日）· `moneyness`（正数一律=虚值）· `is_call` / `is_put`。

**链已剔除 T+1 执行不了的合约**，按希腊字母随便挑都不会挑到开不了的。

---

# 逐日复查：动态止盈止损、分批加减仓

固定比例的止盈止损在入场时就定死了。跟踪止盈、按论据失效了结、分批建仓需要每天重算，
用 `review` 钩子。

```python
from backtest import run
from backtest.engine.review import Add, Close, Reduce

def review(ctx, pos):
    # ③ 跟踪止盈：从最高点回吐三分之一就走
    if pos.mfe_so_far > 0.4 and pos.ratio < pos.mfe_so_far * 0.67:
        return Close("回吐已得利润的三分之一", kind="take_profit")

    # ④ 论据失效——往往比固定止损更早触发
    if ctx.signal("close") < ctx.signal("sma_50"):
        return Close("跌破 50 日线，当初看多的理由没了", kind="stop_loss")

    # ⑤ 中间地带：不是非黑即白
    if pos.days_held > 10 and pos.ratio < 0:
        return Reduce(0.5, "十天没动静，先减一半")

    return None      # 继续持有

r = run(symbol="QQQ", start="2023-01-01", end="2023-06-30",
        entry="close < sma_20 * 0.98",
        exit={"hold_days": 20, "dte_floor": 7},
        engine="event_driven", review=review)
print(len(r.trades), "笔")
```

`pos` 里有 `mfe_so_far` / `mae_so_far`（**截至今天**，不是事后回看）、`ratio`、
`days_held`、`dte`、`iv`、`delta`、`tag`。

两条边界：

- **静态规则仍是安全网。** 时间类了结（`hold_days` / `dte_floor`）不允许被复查绕过，
  否则一个写错的复查函数会把仓位悄悄拖到到期。
- **减仓不产生新的交易记录。** 展期三次的头寸按持仓统计会变成四笔，胜率与持有期全失真。

---

# 接你自己的数据

**合成期权链是测试夹具，不是数据源。** 它的 IV 是常数、没有偏斜、没有期限结构，
所以**期权侧的结论（选什么 delta、止盈设多少）在它上面不成立**。标的侧的结论
（何时进场）可信。

三步，全程有工具：

```python
from backtest.data.adapters import check_file, describe_schema

print(describe_schema())    # 1. 要哪些列、每列什么含义、缺了会怎样
# check_file("你的路径")     # 2. 跑回测之前先体检：致命 / 警告 两档　# 不跑
```

```python
# 不跑（需要你自己的数据文件）
run(..., chain="file:你的路径")                        # 3a. 列名标准
run(..., chain={"kind": "file", "path": "你的路径",     # 3b. 列名对不上
                "column_map": {"trade_date": "asof"}})
```

接受 `.parquet` / `.csv` / 装着它们的目录。缺的可选列会自动补齐（IV 按中价反解、
greeks 按 Black-Scholes 推）**并标记来源**——供应商给的 delta 和推出来的 delta
不是一回事。

换源之后那条"期权侧不可信"的警告会**自动消失**，它由 `source` 字段判定，不是手工开关。
上层（选约、引擎、研究）零改动。

---

# 两个引擎

| | 成交表 | 事件驱动 |
|---|---|---|
| 怎么跑 | 各笔独立，向量化 | 逐日推进，有组合状态 |
| 快慢 | 秒级 | 分钟级 |
| 答什么 | 这个想法**有没有 edge** | 这个策略**能不能跑** |
| 笔数 | **上限**（不受资金约束） | 实际做得到的 |
| 权益曲线 / 回撤 / 夏普 | 没有 | 有 |
| 交易计划 / 逐日复查 / 非固定仓位 | **拒绝**（会说明原因） | 支持 |

同一配置在**无资金约束时两条引擎必须产出逐行一致的交易明细**——这是互相校验的基础，
有测试守着。

---

# 怎么读结果

```python
from backtest import run

r = run(symbol="QQQ", start="2023-01-01", end="2023-06-30",
        exit={"hold_days": 10, "dte_floor": 5}, engine="event_driven")

r.stats()            # 笔数、胜率、盈亏比、平均持有
r.distribution()     # 分位数、尾部、偏度峰度
r.by_exit_reason()   # 按了结原因分组
r.costs()            # 佣金 / 监管费 / 滑点分项，扣费前后对照
r.attribution()      # 盈亏归因：delta / gamma / vega / theta + 残差
r.greeks()           # 每日组合希腊字母
r.stress()           # 压力网格（**超出二阶展开适用范围时拒绝给数**）
r.scenarios()        # 历史情景重放
r.excursion()        # MFE / MAE 分布，带**截断检测**
r.equity_metrics()   # 收益、回撤、夏普、资金占用
r.events             # 运行事件：选约失败、不可成交、顺延、候选被丢弃
r.manifest()         # 配置 + 数据来源 + 依赖版本（可复现的凭据）
```

单笔盈亏**分布**比均值重要得多。期权买方是低胜率、正偏、厚尾：胜率 30% 却赚钱、
或胜率 60% 却亏钱，都很正常。中位数与均值符号相反时报告会显式警告——
那意味着平均收益由少数极值驱动，不可外推。

---

# 三条必须知道的边界

**一、合成链不能调期权侧参数。** 见上面「接你自己的数据」。

**二、成交表引擎没有权益曲线。** 它给的笔数是上限，不是实际做得到的。
要回撤和夏普就用 `engine="event_driven"`。

**三、执行延迟一个交易日。** T 日收盘产生意图，T+1 收盘成交，入场了结都一样。
用 T 日收盘价同时做决策和成交是最常见的前视来源，这里在架构上排除。
副作用：止损触发后次日可能反弹，所以少量"止损"交易最终是盈利的——这是真实成本，不是 bug。

---

# 开发

```bash
python -m tools.check               # 六道守卫
python -m tools.check --fast        # 跳过耗时的两道
python -m tools.status              # 项目状态（从定义算出来）
python -m tools.baseline update     # 更新回归基线（打印前后差异）
```

| 守卫 | 检查什么 |
|---|---|
| 测试套件 | 行为回归；截断重算属性测试覆盖前视偏差 |
| 依赖方向 | 模块层级；定价不得越过数据层；未登记的模块直接失败 |
| 发布完整性 | 本地有、git 里没有的文件 |
| 回归基线 | 结果是否发生非预期变化 |
| 组件定义 | 组件定义自洽；表单字段必须是 `run()` 的参数 |
| 文档代码 | README 里的 python 代码块可执行 |

检查规则与新增守卫的标准见 [规范.md](规范.md)。

## 目录

```
backtest/
  components.py    ← 七个组件各自在交易里干什么、怎么互相牵制（**概念底座**）
  maybe.py         可缺失值——禁止裸 None / NaN / 0 表示缺失
  types.py         ContractId、TradeRecord、了结原因
  pricing.py       Black-Scholes、希腊字母、IV 反解（市场模型，不属于合成链）
  data/
    adapters.py      接你自己的数据：schema、体检、列名映射
    chain_registry.py 数据源注册表，换源只改一个参数
    synthetic/       合成链生成器（测试夹具）
    multi_asset.py   跨市场面板（VIX/VXN/期限结构/美债/信用代理）
  signals/         指标与信号（每个都登记了含义、单位、坑）
  selection/       选约：声明式规则 或 你自己写的函数
  costs/           取价、费用、滑点、可成交性
  portfolio/       账本、现金、实物交割、守恒校验
  risk/            归因、组合希腊字母、压力、VaR、敞口限额、熔断
  engine/
    plan.py          交易计划：一个函数同时决定买什么、买多少、怎么了结
    review.py        逐日复查：动态止盈止损、分批加减仓、headwind
    trade_table.py / event_driven.py
  research/        MFE/MAE、regime 分层、仓位对照
app/               本地网页界面（server.py + static/）
tools/             依赖检查、回归基线
```

`research/` 是你的本地研究记录（运行历史、交易明细、笔记），**不进版本库**——
那是你的资产不是代码，而且 SQLite 二进制没法合并。要备份就单独拷贝这个目录。
