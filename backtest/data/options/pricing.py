"""Black-Scholes prices and greeks.

**This is a substitute for data, not a layer of the backtest.** Nearly its whole
reason to exist is the absence of real option data: a commercial vendor supplies
quotes and greeks directly, and what remains here is a fallback for the fields a
vendor does not fill. Hence the rule that ``selection`` and ``engine`` must not
import it — the layers above consume the chain's columns and do not care whether
those numbers were observed or modelled.

Time to expiry ``t`` is always in years on the **calendar/365** convention
(see config.TimeConventions).

**Why it is not inside ``data/synthetic/``**: pricing is a market model, not the
synthetic chain's private property. The synthetic chain uses it to make prices,
the file source uses it to fill in greeks a vendor omits, attribution uses it to
cross-check. Putting it inside the synthetic chain would imply that removing the
synthetic chain removes pricing — which is false, and would make swapping the
data source look riskier than it is.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from backtest.core.maybe import Maybe
from backtest.core.types import Right

_EPS = 1e-12
#: Search interval when solving for implied volatility.
IV_BOUNDS = (1e-6, 5.0)


def _d1_d2(s, k, t, r, sigma, q):
    vol_sqrt_t = sigma * np.sqrt(t)
    d1 = (np.log(s / k) + (r - q + 0.5 * sigma**2) * t) / vol_sqrt_t
    return d1, d1 - vol_sqrt_t


def _intrinsic(s, k, right: Right):
    return np.maximum(s - k, 0.0) if right is Right.CALL else np.maximum(k - s, 0.0)


def bs_price(s, k, t, r, sigma, q=0.0, right: Right = Right.CALL):
    """Theoretical price of a European option. Scalars and arrays both work.

    Two boundary cases are handled explicitly rather than left to produce NaN or
    divide by zero:

    - ``t == 0``: collapses to intrinsic value.
    - ``sigma == 0``: collapses to the certain payoff discounted at the
      risk-free rate.
    """
    s, k, t, r, sigma, q = map(np.asarray, (s, k, t, r, sigma, q))
    degenerate = (t <= _EPS) | (sigma <= _EPS)
    # Substitute safe values at the degenerate points so log and division do
    # not warn; those results are discarded by the `where` below.
    t_safe = np.where(t <= _EPS, 1.0, t)
    sigma_safe = np.where(sigma <= _EPS, 0.1, sigma)

    d1, d2 = _d1_d2(s, k, t_safe, r, sigma_safe, q)
    disc_s = s * np.exp(-q * t_safe)
    disc_k = k * np.exp(-r * t_safe)
    if right is Right.CALL:
        normal = disc_s * norm.cdf(d1) - disc_k * norm.cdf(d2)
        forward = np.maximum(disc_s - disc_k, 0.0)
    else:
        normal = disc_k * norm.cdf(-d2) - disc_s * norm.cdf(-d1)
        forward = np.maximum(disc_k - disc_s, 0.0)

    zero_t = np.where(t <= _EPS, _intrinsic(s, k, right), forward)
    return np.where(degenerate, zero_t, normal)


def greeks(s, k, t, r, sigma, q=0.0, right: Right = Right.CALL) -> dict[str, np.ndarray]:
    """delta / gamma / vega / theta.

    ``vega`` is the price change for a 1.00 move in volatility (100 vol points);
    ``theta`` is decay per year, on the calendar-day convention.
    """
    s, k, t, r, sigma, q = map(np.asarray, (s, k, t, r, sigma, q))
    degenerate = (t <= _EPS) | (sigma <= _EPS)
    t_safe = np.where(t <= _EPS, 1.0, t)
    sigma_safe = np.where(sigma <= _EPS, 0.1, sigma)

    d1, d2 = _d1_d2(s, k, t_safe, r, sigma_safe, q)
    pdf_d1 = norm.pdf(d1)
    disc_q = np.exp(-q * t_safe)
    disc_r = np.exp(-r * t_safe)
    sqrt_t = np.sqrt(t_safe)

    if right is Right.CALL:
        delta = disc_q * norm.cdf(d1)
        theta = (
            -s * pdf_d1 * sigma_safe * disc_q / (2 * sqrt_t)
            - r * k * disc_r * norm.cdf(d2)
            + q * s * disc_q * norm.cdf(d1)
        )
        expiry_delta = np.where(s > k, 1.0, 0.0)
    else:
        delta = -disc_q * norm.cdf(-d1)
        theta = (
            -s * pdf_d1 * sigma_safe * disc_q / (2 * sqrt_t)
            + r * k * disc_r * norm.cdf(-d2)
            - q * s * disc_q * norm.cdf(-d1)
        )
        expiry_delta = np.where(s < k, -1.0, 0.0)

    gamma = disc_q * pdf_d1 / (s * sigma_safe * sqrt_t)
    vega = s * disc_q * pdf_d1 * sqrt_t

    zeros = np.zeros_like(np.asarray(gamma, dtype=float))
    return {
        "delta": np.where(degenerate, expiry_delta, delta),
        "gamma": np.where(degenerate, zeros, gamma),
        "vega": np.where(degenerate, zeros, vega),
        "theta": np.where(degenerate, zeros, theta),
    }


def price_bounds(s: float, k: float, t: float, r: float, q: float, right: Right) -> tuple[float, float]:
    """Lower and upper bounds on the theoretical price. Used before solving for
    implied volatility to tell whether a given price admits a solution."""
    disc_s = s * np.exp(-q * t)
    disc_k = k * np.exp(-r * t)
    if right is Right.CALL:
        return float(max(disc_s - disc_k, 0.0)), float(disc_s)
    return float(max(disc_k - disc_s, 0.0)), float(disc_k)


def implied_vol(
    price: float,
    s: float,
    k: float,
    t: float,
    r: float,
    q: float = 0.0,
    right: Right = Right.CALL,
) -> Maybe[float]:
    """Solve for implied volatility from a market price.

    With no solution, or no convergence, this returns an **explicit absence**
    rather than raising or passing zero off as an answer — an implied volatility
    read as 0 picks a completely wrong contract and raises nothing.
    """
    if t <= _EPS:
        return Maybe.missing("already expired; implied volatility is undefined")
    if price <= 0:
        return Maybe.missing("price is not positive")

    low, high = price_bounds(s, k, t, r, q, right)
    if price < low - 1e-9:
        return Maybe.missing("price is below the intrinsic-value floor")
    if price > high + 1e-9:
        return Maybe.missing("price is above the theoretical ceiling")

    def objective(sigma: float) -> float:
        return float(bs_price(s, k, t, r, sigma, q, right)) - price

    lo, hi = IV_BOUNDS
    f_lo, f_hi = objective(lo), objective(hi)
    if f_lo > 0 or f_hi < 0:
        return Maybe.missing("the target price lies outside the searchable vol range")
    try:
        root = brentq(objective, lo, hi, xtol=1e-10, rtol=1e-12, maxiter=200)
    except (ValueError, RuntimeError):
        return Maybe.missing("the numerical solve did not converge")
    return Maybe.some(float(root))


def has_early_exercise_value(
    *,
    right: Right,
    option_price: float,
    intrinsic: float,
    strike: float,
    dividend_in_window: float,
    rate: float = 0.0,
    years_to_expiry: float = 0.0,
) -> bool:
    """Whether an American option is worth exercising early, for either right.

    Using a European model for an American option is an **explicit approximation**. For
    out-of-the-money and at-the-money options the error is on the order of a tenth of a
    percent. This does not change the model; it flags the cases where the European price
    is genuinely wrong, so it is not taken as fair by default.

    **Two cases, not one.** This used to answer ``False`` for every put, which is not true
    and is not conservative:

    - **a call before an ex-dividend date**, when its time value is below the dividend:
      exercising captures the dividend, which the European price does not allow for
    - **a deep in-the-money put when rates are above zero**: exercising hands over the
      stock now and receives the strike now, and the strike earns interest for the
      remaining life. The carry on the strike is what makes it worth doing, so the test is
      whether the time value is below that carry.

    The put case needs ``rate`` and ``years_to_expiry``; without them it cannot be
    assessed and this returns ``False`` **because there is nothing to test against**, not
    because the answer is no. The caller that has those figures should pass them -- which
    is why they are arguments rather than a constant read from somewhere.

    It also needs the ``strike``, for the reason given at ``_strike_carry``.
    """
    time_value = option_price - intrinsic
    if intrinsic <= 0:
        return False
    if right is Right.CALL:
        return dividend_in_window > 0 and time_value < dividend_in_window
    # The interest earned on the strike over the remaining life, which is what early
    # exercise of a put buys. A dividend works the other way for a put -- it lowers the
    # stock and so makes holding more attractive -- so it is subtracted.
    carry = _strike_carry(strike, rate, years_to_expiry)
    if carry <= 0:
        return False
    return time_value < carry - dividend_in_window


def _strike_carry(strike: float, rate: float, years: float) -> float:
    """Interest on the strike over the remaining life: ``K x (1 - exp(-r x T))``.

    This used the **intrinsic value** in place of the strike, on the argument that
    intrinsic is a lower bound on the strike for an in-the-money put, so the carry would
    be understated and a flag that errs towards silence is the safe direction.

    The bound is true and the conclusion does not follow. Intrinsic approaches the strike
    only for a put whose underlying has gone to nearly nothing; for an ordinary deep
    in-the-money put it is a small fraction of it, and the figure was out by an order of
    magnitude rather than slightly conservative. At a $400 strike and 4.5% over a year the
    carry is about $18 -- against which a $2 dividend is immaterial -- while $40 of
    intrinsic gives $1.80, which a $2 dividend reverses outright. The flag was being
    decided by the dividend in the one case that is not about dividends.

    The strike is in the caller's hand at every call site, so the approximation bought
    nothing.
    """
    if rate <= 0 or years <= 0:
        return 0.0
    return strike * (1.0 - math.exp(-rate * years))
