"""The invariants, and what each one catches.

An invariant is not a test. A test checks one scenario before the run; an invariant holds
on **every** day of **every** run, and a breach means a code bug rather than a bad input.
So a breach aborts: continuing produces numbers that look normal and are wrong, which is
the one outcome this project refuses.

The architecture document lists five at a minimum. Each has a line here saying what it
would catch, because an invariant whose purpose nobody remembers eventually gets relaxed
to make a failing run pass.

``check_all`` runs them wherever the engine marks the book. Each takes the state it needs
and nothing more, so each can be tested on its own.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

from backtest.core.errors import InvariantError

#: Tolerance for the money identities. Floating-point accumulation over a few thousand
#: trades drifts in the last digits; anything larger is a real error.
TOLERANCE = 1e-6


def cash_not_negative(cash: float, *, when: date, margin: bool = False) -> None:
    """Cash stays non-negative in a non-margin account.

    Catches: an order that was filled without the money to pay for it. Without this,
    capital constraints can be silently violated and the equity curve reports returns on
    money the account never had.
    """
    if margin:
        return
    if cash < -TOLERANCE:
        raise InvariantError(
            f"cash is negative on {when}: {cash:,.2f}. In a non-margin account that "
            f"means a fill was paid for with money the account did not have"
        )


def position_matches_fills(
    positions: Mapping[object, int], cumulative_fills: Mapping[object, int], *, when: date
) -> None:
    """Every position's quantity equals the sum of its fills.

    Catches: a position mutated anywhere but through a fill. The ledger is meant to be
    the only writer; this is what makes that enforceable rather than a convention.
    """
    for key in set(positions) | set(cumulative_fills):
        held = positions.get(key, 0)
        filled = cumulative_fills.get(key, 0)
        if held != filled:
            raise InvariantError(
                f"{key} holds {held} on {when} but its fills sum to {filled}. A position "
                f"changed without a fill, so something other than the ledger wrote to it"
            )


def equity_balances(
    *, equity: float, cash: float, position_value: float, stock_value: float, when: date
) -> None:
    """Equity equals cash plus what is held, at the current multiplier.

    Catches: a contract adjustment (a split, a special dividend) that changed the
    multiplier without revaluing the position.
    """
    expected = cash + position_value + stock_value
    drift = equity - expected
    if abs(drift) > max(TOLERANCE, abs(expected) * 1e-9):
        raise InvariantError(
            f"equity does not balance on {when}, off by {drift:+.6f}: "
            f"cash {cash:,.2f} + options {position_value:,.2f} + stock {stock_value:,.2f} "
            f"= {expected:,.2f}, actual equity {equity:,.2f}"
        )


def daily_pnl_balances(
    *, equity_today: float, equity_yesterday: float, external_flows: float, pnl: float,
    when: date
) -> None:
    """The day's P&L equals the change in equity minus money moved in or out.

    Catches: P&L double-counted or dropped — a partial close booked twice, or a fee
    deducted from cash and not from P&L.
    """
    expected = equity_today - equity_yesterday - external_flows
    drift = pnl - expected
    if abs(drift) > max(TOLERANCE, abs(expected) * 1e-9):
        raise InvariantError(
            f"the day's P&L does not match the equity change on {when}, off by "
            f"{drift:+.6f}: equity moved {equity_today - equity_yesterday:+,.2f} with "
            f"{external_flows:+,.2f} of flows, so P&L should be {expected:+,.2f}, "
            f"reported {pnl:+,.2f}"
        )


def no_expired_position(contracts: Iterable, *, when: date) -> None:
    """Nothing expired is still held.

    Catches: an expiry the settlement step missed. A position left open past its expiry
    keeps being marked against a quote that no longer exists, so the equity curve drifts
    away from reality from that day on and nothing says so.
    """
    stale = [c for c in contracts if getattr(c, "expiry", when) < when]
    if stale:
        raise InvariantError(
            f"{len(stale)} position(s) expired before {when} and are still held: "
            f"{[str(c) for c in stale[:3]]}. The settlement step missed them, so they "
            f"are being marked against a contract that no longer trades"
        )


def check_all(
    *,
    when: date,
    cash: float,
    equity: float,
    position_value: float,
    stock_value: float,
    contracts: Iterable,
    margin: bool = False,
    positions: Mapping[object, int] | None = None,
    cumulative_fills: Mapping[object, int] | None = None,
    pnl: float | None = None,
    equity_yesterday: float | None = None,
    external_flows: float = 0.0,
) -> None:
    """Run every invariant that the state given makes checkable.

    The optional arguments are optional on purpose: an engine that does not track
    cumulative fills cannot check that identity, and **silently skipping is the right
    behaviour only because the caller decides, not this function**. Passing nothing where
    something is available is the one way to weaken this, which is why the engine's call
    site passes everything it has.
    """
    cash_not_negative(cash, when=when, margin=margin)
    equity_balances(equity=equity, cash=cash, position_value=position_value,
                    stock_value=stock_value, when=when)
    no_expired_position(contracts, when=when)
    if positions is not None and cumulative_fills is not None:
        position_matches_fills(positions, cumulative_fills, when=when)
    if pnl is not None and equity_yesterday is not None:
        daily_pnl_balances(equity_today=equity, equity_yesterday=equity_yesterday,
                           external_flows=external_flows, pnl=pnl, when=when)
