# 期权回测平台

研究美股期权策略（QQQ / SPY，中频，几天到几个月）的工具。两个用途：

1. **把策略细节定下来** —— 七个组件各自该怎么设
2. **定型之后长期验证它** —— 优化、证伪、多方验证

> 想知道「现在做到哪了、还差什么」→ **[需求.md](需求.md)**（54 条验收标准，逐条标了状态）
> 想知道「系统长什么样、东西怎么流动」→ **[ROADMAP.md](ROADMAP.md)**

---

## 最快的开始（Mac）

下载这个仓库，**双击 `启动.command`**。它会自己查 Python、装依赖、开浏览器。

第一次要一两分钟装依赖。缺 Python 的话它会告诉你去哪下。

## 手动装

```bash
pip install -e ".[dev]"      # 需要 Python 3.11+
python app/server.py         # 打开 http://localhost:8765
```

## 想给朋友用？

**让他们各自在自己电脑上跑，别做成一个你托管的网站。** 三个原因：

1. 界面里写的交易计划、入场逻辑、自定义信号都是在**服务器上真的执行 Python**。
   自己机器上跑没问题（和 Jupyter 一样）；托管给别人连，等于把"在你电脑上执行
   任意代码"开放出去。
2. 现在**没有鉴权、没有用户隔离**，研究记录是一个共享 SQLite——所有人的策略
   混在一起互相看得见。策略是各人自己的东西。
3. 期权数据通常是**按内部使用授权**的，转发给别人用会违反许可。

各自本地跑则这些全都没有：数据各自的、记录各自的、策略不外流。

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

# 核心：一笔交易是**一个决定**

参数化的写法把策略拆成互不相干的常数：止盈 `1.0`、止损 `0.5`、仓位 `1 张`。
但真实交易里这些是联动的 —— 回撤深的时候你会**同时**买得更久、下注更大、止损放宽。

最典型的是 **④ 止损和 ⑥ 仓位**：

```
仓位规模 = 风险预算 ÷ 止损距离
```

三个量定了两个，第三个就定了。做成两个独立输入框，等于让你填一组自相矛盾的数——
而且系统不会报错，因为它根本不知道这两个数有关系。

## 所以：交易计划

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

# 止盈止损不必是固定比例

在入场那刻把止损定成"亏 50%"，隐含假设是这笔交易的对错开仓时就知道了、而且不会变。
跟踪止盈、按论据失效、分批建仓 —— 都要**每天重算**。

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
python -m tools.check               # ← **提交前跑这个**：六道守卫一起跑
python -m tools.check --fast        #   改动小的时候，跳过耗时的两道
python -m tools.status              #   一屏看完项目到哪了（算出来的，不会过期）
python -m tools.baseline update     #   显式更新基线（会打印前后差异）
```

检查规则与流程见 **[规范.md](规范.md)**。一句话：**能被工具检查的才叫规范，
写在文档里的只是愿望。**

## 怎么保持统一

一个项目失去统一性，几乎总是同一个原因：**同一件事在多处各写一份，然后慢慢走样。**
这个项目踩过几次——界面上挂着"⑤ headwind 完全没有"而减仓早做完了；ROADMAP 写着
"不是验证已定型策略的工具"而它正是。那些话都是手写的，写的时候对，代码往前走了没跟上，
**而且看起来很权威，比没有更糟**。

三条规则：

1. **每个事实只有一处定义。** 组件是什么 → `backtest/components.py`；
   依赖方向 → `tools/check_deps.py`；结果变没变 → `tools/baseline.py`。
2. **派生视图一律生成。** 界面的七个组件表单、缺口清单、`tools/status.py` 的
   全局视图，全是从定义算出来的，不手写。
3. **能被工具检查的约束，别只写在文档里**——文档拦不住代码。

已知还没统一的：`需求.md` 那 54 条验收标准的状态是手写的。像"界面清晰""统计诚实"
这类本来就没法自动判定，所以 `tools/status.py` 只统计它们、并**标明是手写的**，
不假装是算出来的。

六道守卫各拦不同的东西，**每一道都对应一个真实发生过的错误**：

| 守卫 | 拦下过什么 |
|---|---|
| 测试套件 | 行为回归；截断重算属性测试守住前视偏差 |
| 依赖方向 | 定价泄漏到上层；**未登记的模块悄悄绕过检查** |
| 发布完整性 | `.gitignore` 写宽了，整个 `backtest/research/` 包从没上传过 |
| 回归基线 | 改动带来的非预期结果变化 |
| 组件定义 | 界面字段不是 `run()` 真认识的参数，填了没效果还不报错 |
| 文档代码 | README 里的示例跑不通——那比没有文档更糟 |

新增一道守卫的标准：**先有一个具体的错，再有守卫**。没踩过的坑不设防，
否则守卫会变成没人看的噪音。

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
