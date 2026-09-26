"""期权数据源的注册与构造。

**这个模块存在的唯一目的，是让"换数据源"只改一个参数。**

上层（引擎、选约、研究）只认 ``ChainSource`` 协议，不认具体实现。所以换源不需要
改它们中的任何一行——但前提是构造数据源的地方不能把实现写死。这里就是那个地方。

已注册的源：

``"synthetic"``（默认）
    Black-Scholes 生成的合成链。**它是测试夹具，不是数据源。** IV 是常数、无偏斜、
    无期限结构，所有期权侧的结论在它上面都不成立。用它只是为了在没有真实数据时
    让整条管道跑得起来。

``"file:<路径>"``
    你自己的数据。见 ``backtest.data.adapters``——那里有需要的列、体检工具、
    以及列名对不上时的映射方式。

自己加一种源：``register("名字", 工厂函数)``，工厂签名 ``(spec, market, config)``。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from backtest.config import BacktestConfig
from backtest.errors import StartupError

#: 名字 → 工厂。工厂签名 ``(spec: str, market, config) -> ChainSource``。
_REGISTRY: dict[str, Callable[..., Any]] = {}


def register(name: str, factory: Callable[..., Any]) -> None:
    """注册一种数据源。重名直接报错——静默覆盖会让人找不到自己在用哪个实现。"""
    if name in _REGISTRY:
        raise StartupError(f"数据源 {name!r} 已经注册过了")
    _REGISTRY[name] = factory


def available() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def build_chain_source(spec: Any, market, config: BacktestConfig):
    """把 ``chain=`` 参数变成一个真的数据源。

    接受三种东西：``None``（用默认的合成链）、``"名字"`` 或 ``"名字:参数"``、
    以及任何已经实现了协议的对象（原样返回）。
    """
    if spec is None:
        return _REGISTRY["synthetic"](None, market, config)

    if isinstance(spec, str):
        name, _, rest = spec.partition(":")
        return _dispatch(name, {"path": rest} if rest else {}, market, config)

    if isinstance(spec, Mapping):
        opts = dict(spec)
        name = opts.pop("kind", None)
        if name is None:
            raise StartupError(
                'chain 字典里要有 kind，例如 '
                '{"kind": "file", "path": "/路径", "column_map": {"trade_date": "asof"}}'
            )
        return _dispatch(str(name), opts, market, config)

    # 已经是个数据源对象。只查协议，不查类型——鸭子类型才是这个接口的意义。
    for method in ("snapshot", "series"):
        if not callable(getattr(spec, method, None)):
            raise StartupError(
                f"传进来的 chain 不是合法的数据源：缺少 {method}() 方法。"
                f"需要实现 backtest.data.chain.ChainSource 协议。"
            )
    return spec


def _dispatch(name: str, opts: dict, market, config):
    factory = _REGISTRY.get(name)
    if factory is None:
        raise StartupError(
            f"未知的数据源 {name!r}。已注册：{list(available())}。"
            f'用自己的数据写 chain="file:/路径"；列名对不上就写字典形式：'
            f'chain={{"kind": "file", "path": "/路径", "column_map": {{"你的列名": "标准列名"}}}}'
        )
    return factory(opts, market, config)


def _synthetic(opts, market, config):
    from backtest.data.synthetic.generator import SyntheticChainSource

    return SyntheticChainSource(market, config)


def _file(opts, market, config):
    """``chain="file:/路径"`` 或
    ``chain={"kind":"file", "path":..., "column_map":{...}, "scale":{"iv":0.01}}``。

    ``scale`` 换单位，最常见的是 IV 写成了百分数。"""
    from backtest.data.adapters import FileChainSource

    path = opts.get("path")
    if not path:
        raise StartupError('file 源需要路径：chain="file:/路径/到/你的数据"')
    return FileChainSource(
        path, market, config,
        column_map=opts.get("column_map"),
        scale=opts.get("scale"),
        strict=bool(opts.get("strict", True)),
    )


register("synthetic", _synthetic)
register("file", _file)
