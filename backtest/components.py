"""七个策略组件：各自在交易里起什么作用，以及它们怎么互相牵制。

**这个模块是整个项目的概念底座。** 界面怎么分区、引擎在哪里留钩子、研究工具该
回答什么问题，都从这里推出来，而不是反过来。

## 为什么不是"七个平等的模块"

按组件表把它们并排列出来，会看不见最要紧的东西：**它们互相决定**。

最典型的是 ④ 止损和 ⑥ 仓位。真实交易里：

    仓位规模 = 风险预算 ÷ 止损距离

三个量里定了两个，第三个就定了。把"止损 0.5"和"1 张"做成两个独立输入框，等于
让使用者填一组自相矛盾的数——而且系统不会报错，因为它根本不知道这两个数有关系。

写清楚每个组件在交易里干什么之后，联动是自己冒出来的，见 :data:`COUPLINGS`。

## 四层，不是七块

按**交易里发生的顺序**分，七个组件落在四层上：

===========  ===========================  ==========================================
层            组件                          为什么单独成一层
===========  ===========================  ==========================================
决定之前      ①                            它不下单，只决定「今天做不做、用哪一档」
一笔的决定    ② ②b ③ ④ ⑥                  这几件事是**同一个判断**的几个侧面
持仓期间      ⑤                            论据部分失效时的中间地带，止损是非黑即白
组合层        ⑦                            不属于任何单笔
===========  ===========================  ==========================================

这个分层直接决定了代码结构：「一笔的决定」那层对应 :class:`backtest.engine.plan.Plan`
（一个函数同时产出合约、仓位、了结点）；「持仓期间」需要的是另一种钩子——
逐日复查，目前还没有。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Layer = Literal["决定之前", "一笔的决定", "持仓期间", "组合层"]
Status = Literal["done", "partial", "missing"]


#: 一个组件的细节是怎么定下来的。这个区分决定了平台该给什么工具。
#:
#: ``"configured"``
#:     直接填就行。你已经知道要什么，系统照做。② 选约、③④ 的数值属于这类
#:     （前提是有真实数据）。
#: ``"discovered"``
#:     **填不出来，得先研究**。你不可能先验地知道"VIX>25 且期限结构<1"是对的切法，
#:     也不知道什么信号代表"论据开始失效"。这类组件需要的是**发现流程**，
#:     不是配置框——给个输入框反而有害，因为它暗示你本该知道答案。
Origin = Literal["configured", "discovered"]


@dataclass(frozen=True, slots=True)
class Field:
    """一个组件要录入的一个字段。

    **界面从这里生成，不手写 HTML。** 手写的话，这份定义和界面会各说各的：
    改了组件的含义却忘了改界面，或者反过来。实测踩过——界面上挂着一条早就
    不成立的说明，看起来还很权威。
    """

    name: str                       # 表单字段名，要与后端 payload 对得上
    label: str
    kind: str                       # number / ratio / int / select / text / code
    default: Any = None
    unit: str = ""
    help: str = ""
    options: tuple[tuple[str, str], ...] = ()     # select：(值, 显示文字)
    #: 仅当另一字段取某值时才显示，写成 "字段名=值"。
    depends: str = ""
    #: 取值范围，用于校验与 number 控件。
    lo: float | None = None
    hi: float | None = None


@dataclass(frozen=True, slots=True)
class Component:
    key: str
    name: str
    layer: Layer
    #: **在一笔真实交易里，这个组件到底决定什么。** 这一句是全模块的重点。
    role: str
    #: 它输出什么（不是「它怎么算」，是「它给出什么」）。
    output: str
    status: Status
    #: 这个组件的细节是填出来的还是研究出来的。见 :data:`Origin`。
    origin: Origin = "configured"
    #: 当前欠什么。``status`` 为 done 时为空。
    gap: str = ""
    #: ``origin`` 为 discovered 时：**怎么找**。没有这个，组件就只能靠拍脑袋。
    how_to_find: str = ""
    #: 这个组件要录入什么。界面按它生成表单。空元组表示这个组件当前无法配置。
    inputs: tuple[Field, ...] = ()
    #: 这个组件在「交易计划」模式下由计划函数的哪一部分决定。
    #: 有值就说明它和别的组件是联动的，分开填会打架。
    in_plan: str = ""


COMPONENTS: tuple[Component, ...] = (
    Component(
        "setup", "① setup / regime", "决定之前",
        role="决定现在该不该做这类交易、以及用哪一套参数。**它自己不产生交易。**",
        output="开关 + 倍率",
        status="partial",
        origin="discovered",
        gap="规则型分类（VIX / 期限结构 / 趋势）已有，但没接成调制器；统计型（HMM）不做，见 placeholder",
        how_to_find=(
            "不要先定切法再验证——那是先射箭后画靶。正确顺序是：\n"
            "1. 先跑一版**不做任何 regime 条件**的策略，拿到全部交易\n"
            "2. 对每个候选维度（VIX 水平、期限结构、趋势、信用利差、已实现波动…）"
            "分层看表现\n"
            "3. 三个问题同时问：分开了吗（效应量）· 每档样本够吗 · 换个子区间还成立吗\n"
            "4. **试了几个维度必须记下来**——分层探索在统计上等同扫参数，"
            "试十个挑最好的，那个'最好'里有多少是运气\n"
            "5. 只有三个问题都过关的维度才值得做成条件\n"
            "工具：backtest.research.discovery.regime_candidates()"
        ),
    ),
    Component(
        "entry", "② entry", "一笔的决定",
        role="决定时点——就是今天，不是上周也不是下周。",
        output="做 / 不做",
        status="done",
        in_plan="return None 之前的那段判断",
        inputs=(
            # 同一件事两种填法，二选一。两个框同时摆出来的话，使用者不知道
            # 哪个生效，而后端只能猜——猜错就是跑了另一个策略。
            Field("entry_mode", "怎么写", "select", "expr", options=(
                ("expr", "表达式"), ("code", "代码"))),
            Field("entry", "入场条件", "text", "close < sma_20 * 0.98",
                  depends="entry_mode=expr",
                  help="只支持比较、and/or/not 与四则运算"),
            Field("entry_code", "entry(view)", "code", "",
                  depends="entry_mode=code",
                  help="返回 True 就入场。缺失信号一律不入场"),
        ),
    ),
    Component(
        "select", "②b 选合约", "一笔的决定",
        role="把「我看多」落成「具体哪一张」。方向之外还决定了这笔的希腊字母暴露。",
        output="一张合约",
        status="done",
        in_plan="Plan(contract=…)",
        inputs=(
            Field("right", "类型", "select", "call",
                  options=(("call", "看涨 call"), ("put", "看跌 put"))),
            Field("dte", "目标剩余天数", "int", 30, unit="日历日", lo=3, hi=200,
                  help="按日历日算，与市场惯例一致"),
            Field("strike_mode", "怎么定行权价", "select", "delta",
                  options=(("delta", "按目标 delta"), ("moneyness", "按价值状态"))),
            Field("delta", "目标 delta", "ratio", 0.30, lo=0.05, hi=0.95,
                  depends="strike_mode=delta"),
            Field("moneyness", "虚值比例", "ratio", 0.05, lo=-0.5, hi=0.5,
                  depends="strike_mode=moneyness", help="正数为虚值"),
        ),
    ),
    Component(
        "take_profit", "③ take profit", "一笔的决定",
        role="决定「赚到什么程度算兑现了」。**它的位置由入场理由决定**——"
             "你为什么进，决定了什么算「这事发生了」。",
        output="兑现点",
        status="partial",
        in_plan="Plan(take_profit=…)",
        inputs=(
            Field("take_profit", "止盈", "ratio", 1.0, unit="×", lo=0,
                  help="相对入场价。1.0 = 赚 100% 就走"),
        ),
        gap="逻辑完整，但数值在合成链上不可信；MFE 分布可用于定档",
    ),
    Component(
        "stop_loss", "④ stop loss", "一笔的决定",
        role="决定「什么情况说明我错了」。**止损不是「亏了多少」，是「论据失效」**——"
             "这个区别决定了它该放在哪里。",
        output="论据失效点",
        status="partial",
        in_plan="Plan(stop_loss=…)",
        inputs=(
            Field("stop_loss", "止损", "ratio", 0.5, unit="×", lo=0, hi=1,
                  help="相对入场价。0.5 = 亏 50% 就走。买方最大损失就是权利金"),
            Field("hold_days", "最多持有", "int", 10, unit="交易日", lo=1, hi=120),
            Field("dte_floor", "剩余到期下限", "int", 5, unit="交易日", lo=2, hi=60,
                  help="到这个剩余期限强制了结，避免被拖到到期"),
        ),
        gap="逻辑完整，但数值在合成链上不可信；MAE 分布可用于定档，注意截断",
    ),
    Component(
        "headwind", "⑤ headwind", "持仓期间",
        role="论据**部分**失效时怎么办。止损是非黑即白，这里是中间地带："
             "减仓、展期、对冲掉某一段风险。",
        output="减仓 / 展期 / 维持",
        status="partial",
        inputs=(
            Field("review_code", "逐日复查", "code", "",
                  help="定义 review(ctx, pos)。每天对每个持仓问一遍，返回 "
                       "Close / Reduce / Add 或 None。跟踪止盈、论据失效、"
                       "分批减仓都写在这里。"),
        ),
        origin="discovered",
        gap="机制有了（逐日复查可以减仓 / 动态平仓），但**怎么找**还没有——"
            "哪些信号代表论据开始失效，得从轨迹数据里研究出来；展期未实现",
        how_to_find=(
            "核心问题：**结果不好的那些持仓，事前有没有可观察的预警。**\n"
            "1. 需要**逐日持仓轨迹**——只有入场和出场两个点，看不出中间发生了什么。"
            "这是硬前置，没有它这项研究做不了\n"
            "2. 把交易按最终结果分成两组，看持有第 k 天时两组的状态有没有分开："
            "标的走势、IV 变化、delta 漂移、距止损多远、至今的 MFE/MAE\n"
            "3. 对每个候选预警信号问：多早触发 · 命中率 · 误报率 · "
            "照它做会怎样（对照不做）\n"
            "4. **陷阱：止损会截断数据。** 止损设在 -50%，你永远看不到那些"
            "本来会跌到 -80% 再反弹的路径。研究 headwind 必须先跑一版"
            "**关掉主动出场**的，才能观察完整轨迹\n"
            "工具：backtest.research.discovery.headwind_signals()（未实现，前置数据已就绪）"
        ),
    ),
    Component(
        "position", "⑥ positioning", "一笔的决定",
        role="决定「这个判断值多少钱」。**与 ④ 是同一个数的两面**："
             "风险预算 ÷ 止损距离 = 规模。",
        output="张数",
        status="partial",
        in_plan="Plan(risk_pct / notional / contracts)",
        inputs=(
            Field("sizing_method", "怎么定规模", "select", "risk_pct", options=(
                ("risk_pct", "每笔风险占权益固定比例"),
                ("fixed", "固定张数"),
                ("fixed_notional", "每笔固定金额"),
                ("vol_target", "波动率目标"),
                ("kelly", "分数 Kelly"),
            )),
            Field("risk_pct", "每笔风险", "ratio", 0.02, unit="× 权益", lo=0.001, hi=1,
                  depends="sizing_method=risk_pct",
                  help="每笔亏光时正好亏掉权益的这个比例"),
            Field("quantity", "张数", "int", 1, lo=1, hi=50, depends="sizing_method=fixed",
                  help="最好懂也最差：权益变化后风险占比会漂移，而你不会察觉"),
            Field("notional", "每笔投入", "number", 5000, unit="美元", lo=100,
                  depends="sizing_method=fixed_notional",
                  help="投入固定但风险不固定——便宜的合约会买到很多张"),
            Field("target_vol", "目标年化波动", "ratio", 0.20, lo=0.01, hi=2,
                  depends="sizing_method=vol_target", help="控的是组合波动，不是单笔亏损"),
            Field("kelly_fraction", "用几分之一 Kelly", "ratio", 0.25, lo=0.01, hi=1,
                  depends="sizing_method=kelly",
                  help="参数用回测期内已了结的交易估——实盘当下你不知道未来胜率，只能当上界看"),
        ),
        gap="计划式下可按 risk_pct / notional 定；参数模式仍是独立的固定张数，会与止损打架",
    ),
    Component(
        "hedge", "⑦ hedge", "组合层",
        role="决定「我不想承担的那部分风险怎么剥离」。**不属于任何单笔。**",
        output="组合层的限额与对冲头寸",
        status="partial",
        gap="敞口限额、回撤熔断、压力测试、剔除对冲对照已有；界面未接；多腿未做",
    ),
)

#: 组件之间的牵制关系。**这些不是"设计上的耦合"，是交易本身就有的关系**——
#: 系统把它们拆开，不代表它们真的独立。
COUPLINGS: tuple[tuple[str, str, str], ...] = (
    ("stop_loss", "position",
     "风险预算 ÷ 止损距离 = 规模。三个量定了两个，第三个就定了——分开填必然打架"),
    ("entry", "take_profit",
     "入场理由决定什么算「兑现了」。理由换了，止盈位置跟着换"),
    ("entry", "stop_loss",
     "入场理由决定什么算「我错了」。止损该放在论据失效的地方，不是某个固定百分比"),
    ("setup", "position",
     "regime 调制规模：同一个信号在不同环境下不该下一样的注"),
    ("setup", "select",
     "regime 调制选约：高波动环境下该选更久、更实值的合约"),
    ("stop_loss", "headwind",
     "headwind 是止损的连续版本。只有止损意味着只能非黑即白地处理论据失效"),
)

#: 四层的顺序与它们各自要什么。
LAYERS: tuple[tuple[Layer, str], ...] = (
    ("决定之前", "开关和倍率。不下单。"),
    ("一笔的决定", "一个判断同时产出：买什么、买多少、怎么了结。"),
    ("持仓期间", "逐日复查：论据还成立吗？不成立到什么程度？"),
    ("组合层", "跨头寸的限额与对冲。"),
)


def as_form_spec() -> list[dict]:
    """界面要的那份定义：四层、每层的组件、每个组件的录入字段。

    **界面从这里生成，不手写。** 这样"这个组件是干什么的"只有一处定义，
    改了自然同步；手写的话两边一定会走样。
    """
    from dataclasses import asdict

    out = []
    for layer, what in LAYERS:
        out.append({
            "layer": layer,
            "what": what,
            "components": [{
                "key": c.key, "name": c.name, "role": c.role, "output": c.output,
                "status": c.status, "origin": c.origin, "gap": c.gap,
                "how_to_find": c.how_to_find, "in_plan": c.in_plan,
                "inputs": [asdict(f) for f in c.inputs],
            } for c in by_layer(layer)],
        })
    return out


def by_layer(layer: Layer) -> tuple[Component, ...]:
    return tuple(c for c in COMPONENTS if c.layer == layer)


def gaps() -> list[str]:
    """当前撑不起来的组件。界面要让缺口一眼可见，而不是假装完整。

    从 :data:`COMPONENTS` 生成，不手写——手写的清单会在功能补上之后忘了改，
    于是界面长期显示一条早就不成立的警告。
    """
    return [f"{c.name}：{c.gap}" for c in COMPONENTS if c.status != "done" and c.gap]


def describe():
    """七个组件的职责表。界面、文档、notebook 都读这一份。"""
    import pandas as pd

    return pd.DataFrame([{
        "组件": c.name, "层": c.layer, "在交易里决定什么": c.role,
        "输出": c.output,
        "状态": {"done": "✅", "partial": "⚠️", "missing": "○"}[c.status],
        "欠什么": c.gap,
    } for c in COMPONENTS]).set_index("组件")


def describe_couplings():
    """组件之间的牵制关系。看这张表就知道哪些参数不该分开填。"""
    import pandas as pd

    names = {c.key: c.name for c in COMPONENTS}
    return pd.DataFrame([
        {"甲": names[a], "乙": names[b], "关系": why} for a, b, why in COUPLINGS
    ])
