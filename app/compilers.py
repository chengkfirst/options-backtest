"""Turning the Python a user writes in the browser into callables.

Four jobs, one shape: compile, check that the expected name is defined, and on any
failure raise the **whole** error back to the front end. Seeing "it failed" in a browser
without knowing where is worse than a crash, which is why none of these swallows
anything.

The code runs **on your machine, in your process**, exactly as it would in Jupyter. The
service listens only on 127.0.0.1.

The help texts live here too, beside the thing they describe, so a parameter added to
``ctx`` and its documentation cannot drift apart.
"""

from __future__ import annotations

from typing import Any

USER_CODE_HELP = """What is available:

  view["sma_20"]        the signal's value, as a Maybe
    .is_missing         True during the warm-up or at a data gap
    .unwrap()           take the number out (raises when absent, so test first)
  view.day              the day (datetime.date)
  view.names()          every registered signal name

It must return True or False. Returning False when any signal is absent (no entry) is
the convention — an absence must never take part in a comparison as 0."""


PLAN_HELP = """The ctx a plan function receives:

  ctx.day                 the decision day
  ctx.spot                the underlying's price that day
  ctx.chain               every executable contract that day (a DataFrame, one per row)
  ctx.calls / ctx.puts    the same, already filtered by type
  ctx.equity              current equity        <- what (6) sizing reads
  ctx.cash                available cash
  ctx.positions           how many positions are open
  ctx.drawdown            the current drawdown (negative, e.g. -0.08)
  ctx.signal(name, default=...)  read a signal; absent with no default raises, and it
                                 never quietly returns 0
  ctx.view[name]          the raw Maybe, for handling absence yourself

The columns of chain / calls / puts:
  iv delta gamma vega theta   the greeks and implied volatility
  bid ask mid spread          the quote; spread is the relative bid/ask spread
  dte                         days to expiry in calendar days (the market convention)
  sessions_left               days to expiry in trading days (the executability basis)
  moneyness                   positive always means out of the money, calls and puts alike
  strike expiry right         the contract's identity
  is_call / is_put            booleans, so "C"/"P" need not be remembered

The chain already has anything unexecutable on T+1 removed, so picking freely on the
greeks cannot land on something that will not open.

The fields of Plan:
  contract      one row picked from chain (required)
  contracts / risk_pct / notional    size; at most one, else the global sizing method
  take_profit / stop_loss            omitted falls back to the global value
  max_hold / dte_floor               the same
  tag           a grouping tag, stored with the trade for comparison afterwards
  note          one sentence for a person, also stored with the trade
"""


def compile_plan(code: str):
    """Compile the user's Python into a plan function.

    It executes in this process on this machine, as ``compile_entry`` does. A compile or
    execution error is raised back to the front end in full.
    """
    namespace: dict[str, Any] = {}
    from backtest.signals.plan import Plan

    namespace["Plan"] = Plan
    try:
        exec(compile(code, "<your trade plan>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(
            f"syntax error in the plan code, line {exc.lineno}: {exc.msg}\n\n{PLAN_HELP}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"the plan code raised: {type(exc).__name__}: {exc}") from exc

    fn = namespace.get("plan")
    if fn is None or not callable(fn):
        raise ValueError(
            "the plan code must define a function named plan.\n\n" + PLAN_HELP
        )
    return fn


def compile_review(code: str):
    """Compile the user's daily review function. ``Close`` / ``Reduce`` / ``Add`` are put
    straight into its namespace."""
    from backtest.signals.review import Add, Close, Reduce

    namespace: dict[str, Any] = {"Close": Close, "Reduce": Reduce, "Add": Add}
    try:
        exec(compile(code, "<your daily review>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(f"syntax error in the review code, line {exc.lineno}: "
                         f"{exc.msg}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"the review code raised: {type(exc).__name__}: {exc}") from exc
    fn = namespace.get("review")
    if fn is None or not callable(fn):
        raise ValueError(
            "the review code must define a function named review:\n\n"
            "def review(ctx, pos):\n"
            "    if pos.mfe_so_far > 0.4 and pos.ratio < pos.mfe_so_far * 0.67:\n"
            "        return Close('gave back a third', kind='take_profit')\n"
            "    return None"
        )
    return fn


def compile_entry(code: str):
    """Compile the user's Python into an entry function.

    This code runs **on your machine, in your process**, exactly as it would in Jupyter.
    The service listens only on 127.0.0.1.

    A compile or execution error is raised back to the front end in full — seeing "it
    failed" in the browser without knowing where is worse than a crash.
    """
    namespace: dict[str, Any] = {}
    try:
        exec(compile(code, "<your entry logic>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(
            f"syntax error in the entry code, line {exc.lineno}: {exc.msg}\n\n"
            f"{USER_CODE_HELP}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"the entry code raised: {type(exc).__name__}: {exc}") from exc

    fn = namespace.get("entry")
    if fn is None or not callable(fn):
        raise ValueError(
            "the entry code must define a function named entry, for example:\n\n"
            "def entry(view):\n"
            "    rsi = view['rsi_14']\n"
            "    if rsi.is_missing:\n"
            "        return False\n"
            "    return rsi.unwrap() < 30\n\n" + USER_CODE_HELP
        )

    def guarded(view):
        try:
            out = fn(view)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"the entry function raised on {getattr(view, 'day', '?')}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(out, bool):
            raise RuntimeError(
                f"the entry function must return True or False; on "
                f"{getattr(view, 'day', '?')} it returned {type(out).__name__}: {out!r}"
            )
        return out

    guarded.__name__ = "user_entry_logic"
    return guarded


def compile_signals(code: str) -> dict[str, Any]:
    """Compile the user's Python into custom signals.

    The convention: define a dict named SIGNALS, ``{"name": lambda bars: a Series}``.
    ``bars.adjusted("close")`` gives adjusted prices — signals always use adjusted
    prices, or ex-dividend days produce false gaps.
    """
    namespace: dict[str, Any] = {}
    try:
        exec(compile(code, "<your signals>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(f"syntax error in the signal code, line {exc.lineno}: "
                         f"{exc.msg}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"the signal code raised: {type(exc).__name__}: {exc}") from exc

    signals = namespace.get("SIGNALS")
    if signals is None:
        raise ValueError(
            "the signal code must define a dict named SIGNALS, for example:\n\n"
            "SIGNALS = {\n"
            "    'ma20_slope': lambda b: b.adjusted('close').rolling(20).mean().pct_change(5),\n"
            "}"
        )
    if not isinstance(signals, dict) or not all(callable(v) for v in signals.values()):
        raise ValueError("SIGNALS must be a dict of {name: function}")
    return signals


