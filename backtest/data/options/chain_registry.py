"""Registering and building option data sources.

**This module exists for one purpose: so that swapping the data source changes
one argument.**

The layers above (engine, selection, research) know only the ``ChainSource``
protocol, never a concrete implementation, so a swap needs no change in any of
them — provided the place that constructs the source does not hard-code one.
This is that place.

Registered sources:

``"synthetic"`` (the default)
    A chain generated with Black-Scholes. **It is a test fixture, not a data
    source.** Its IV is constant, with no skew and no term structure, and no
    option-side conclusion holds on it. Its only use is getting the whole
    pipeline to run when there is no real data.

``"file:<path>"``
    Your own data. See ``backtest.data.options.adapters`` for the columns needed, the
    health check, and how to map column names that do not line up.

To add a source: ``register("name", factory)``, where the factory takes
``(spec, market, config)``.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from backtest.core.config import BacktestConfig
from backtest.core.errors import StartupError

#: name -> factory, where the factory is
#: ``(spec: str, market, config) -> ChainSource``.
_REGISTRY: dict[str, Callable[..., Any]] = {}


def register(name: str, factory: Callable[..., Any]) -> None:
    """Register a data source. A duplicate name raises — silently overwriting one
    leaves no way to tell which implementation is in use."""
    if name in _REGISTRY:
        raise StartupError(f"the data source {name!r} is already registered")
    _REGISTRY[name] = factory


def available() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def build_chain_source(spec: Any, market, config: BacktestConfig):
    """Turn the ``chain=`` argument into an actual data source.

    Three things are accepted: ``None`` (the default synthetic chain), a
    ``"name"`` or ``"name:argument"`` string, and any object that already
    implements the protocol (returned as is).
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
                'a chain dict needs a kind, e.g. '
                '{"kind": "file", "path": "/path", "column_map": {"trade_date": "asof"}}'
            )
        return _dispatch(str(name), opts, market, config)

    # Already a source object. Check the protocol, not the type — duck typing is
    # the whole point of this interface.
    for method in ("snapshot", "series"):
        if not callable(getattr(spec, method, None)):
            raise StartupError(
                f"the chain passed in is not a valid data source: no {method}() "
                f"method. It must implement the backtest.data.chain.ChainSource "
                f"protocol."
            )
    return spec


def _dispatch(name: str, opts: dict, market, config):
    factory = _REGISTRY.get(name)
    if factory is None:
        raise StartupError(
            f"unknown data source {name!r}. Registered: {list(available())}. "
            f'For your own data write chain="file:/path"; when the column names do '
            f'not line up, use the dict form: '
            f'chain={{"kind": "file", "path": "/path", "column_map": {{"your name": "standard name"}}}}'
        )
    return factory(opts, market, config)


def _synthetic(opts, market, config):
    """``chain="synthetic"``, or ``chain={"kind":"synthetic"}``.

    **It takes no options, and says so rather than ignoring them.** It used to accept
    whatever it was handed and use none of it, so ``chain={"kind":"synthetic",
    "clean":["crossed_quote"]}`` ran with no cleaning at all and raised nothing -- the
    exact failure this project refuses everywhere else: a researcher who believes a rule
    is in force and is reading the result without it.

    Cleaning in particular cannot mean anything here. The generator prices every row from
    Black-Scholes on the underlying's close, so there are no crossed quotes, no quotes
    below intrinsic and no missing quotes for a rule to find -- a rule set would report
    zero rows touched and look as though the data were clean, when the truth is that it
    was never dirty.
    """
    from backtest.data.options.synthetic.generator import SyntheticChainSource

    unknown = sorted(k for k in (opts or {}) if k != "kind")
    if unknown:
        raise StartupError(
            f"the synthetic chain takes no options and was given {unknown}. Its prices "
            f"come from Black-Scholes on the underlying's close, so there is nothing to "
            f"clean, map or rescale. "
            + ("Cleaning rules belong to a real data source: use "
               'chain={"kind": "file", "path": ..., "clean": [...]}. '
               if "clean" in unknown else "")
            + "The generator's own parameters are set with synthetic=SyntheticChainConfig(...)"
        )
    return SyntheticChainSource(market, config)


def _file(opts, market, config):
    """``chain="file:/path"``, or
    ``chain={"kind":"file", "path":..., "column_map":{...}, "scale":{"iv":0.01}}``.

    ``scale`` converts units; the common case is IV stored as a percentage."""
    from backtest.data.options.adapters import FileChainSource

    path = opts.get("path")
    if not path:
        raise StartupError('the file source needs a path: chain="file:/path/to/your/data"')
    return FileChainSource(
        path, market, config,
        column_map=opts.get("column_map"),
        scale=opts.get("scale"),
        strict=bool(opts.get("strict", True)),
        cleaning=_cleaning_from(opts.get("clean")),
        iv_model=str(opts.get("iv_model", "european")),
    )


def _cleaning_from(given):
    """Cleaning rules from the config, by name.

    ``clean=["crossed_quote", "no_quote", {"wide_spread": 0.20}]``. Named rather than
    passed as objects so a run's cleaning survives into the YAML config file and back --
    a rule set that cannot be written down is a rule set a run cannot reproduce.

    ``None`` means no rules. There is no default set, for the reason in
    ``data/options/clean.py``: a platform-chosen cleaning rule is the platform deciding
    which market conditions a strategy is tested through.
    """
    if given is None:
        return None
    from backtest.data.options import clean as rules

    BY_NAME = {
        "crossed_quote": rules.crossed_quote,
        "below_intrinsic": rules.below_intrinsic,
        "no_quote": rules.no_quote,
        "wide_spread": rules.wide_spread,
    }
    built = []
    for item in given:
        if isinstance(item, str):
            name, arg = item, None
        elif isinstance(item, Mapping) and len(item) == 1:
            (name, arg), = item.items()
        else:
            raise StartupError(
                f"a cleaning rule is a name or a one-entry mapping of name to its "
                f"threshold, got {item!r}"
            )
        maker = BY_NAME.get(name)
        if maker is None:
            raise StartupError(
                f"{name!r} is not a cleaning rule; one of {', '.join(sorted(BY_NAME))}"
            )
        if name == "wide_spread":
            if arg is None:
                raise StartupError(
                    "wide_spread needs its threshold: "
                    "{\"wide_spread\": 0.20} means a spread up to 20% of the mid is "
                    "tradable. Which spreads a strategy can live with is the "
                    "researcher's choice, not the platform's"
                )
            built.append(maker(max_share_of_mid=float(arg)))
        elif name == "below_intrinsic" and arg is not None:
            built.append(maker(tolerance=float(arg)))
        else:
            if arg is not None:
                raise StartupError(f"{name!r} takes no threshold, got {arg!r}")
            built.append(maker())
    return rules.Cleaning(rules=tuple(built))


register("synthetic", _synthetic)
register("file", _file)
