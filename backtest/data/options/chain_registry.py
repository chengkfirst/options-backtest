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
    from backtest.data.options.synthetic.generator import SyntheticChainSource

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
    )


register("synthetic", _synthetic)
register("file", _file)
