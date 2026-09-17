"""Black-Scholes 定价与希腊字母。

**这是合成链的脚手架，不是回测系统的一层。** 它存在的理由几乎全是"没有真实期权
数据"：商业数据源直接提供报价与希腊字母，届时这里只剩盈亏归因在数据商缺字段时的
回退用途。因此它位于 ``data/synthetic/`` 之下，``selection`` 与 ``engine``
不得直接导入——上层消费的是链上的字段，不关心那些数字是观测还是模型生成。

到期时间 ``t`` 一律是**日历日 / 365** 的年化值（见 config.TimeConventions）。
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from backtest.maybe import Maybe
from backtest.types import Right

_EPS = 1e-12
#: 隐含波动率反解的搜索区间。
IV_BOUNDS = (1e-6, 5.0)


def _d1_d2(s, k, t, r, sigma, q):
    vol_sqrt_t = sigma * np.sqrt(t)
    d1 = (np.log(s / k) + (r - q + 0.5 * sigma**2) * t) / vol_sqrt_t
    return d1, d1 - vol_sqrt_t


def _intrinsic(s, k, right: Right):
    return np.maximum(s - k, 0.0) if right is Right.CALL else np.maximum(k - s, 0.0)


def bs_price(s, k, t, r, sigma, q=0.0, right: Right = Right.CALL):
    """欧式期权理论价。支持标量与数组。

    两个边界情形被显式处理，而不是任其产生 NaN 或除零：

    - ``t == 0``：退化为内在价值。
    - ``sigma == 0``：退化为按无风险利率贴现的确定性收益。
    """
    s, k, t, r, sigma, q = map(np.asarray, (s, k, t, r, sigma, q))
    degenerate = (t <= _EPS) | (sigma <= _EPS)
    # 在退化处填入安全值再计算，避免 log/除零产生警告；结果随后被 where 丢弃
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
    """delta / gamma / vega / theta。

    ``vega`` 为波动率变动 1.00（即 100 个波动率点）对应的价格变化；
    ``theta`` 为每年的时间衰减，按日历日口径。
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
    """理论价的上下界。反解隐含波动率前用它判断给定价格是否有解。"""
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
    """由市场价格反解隐含波动率。

    无解或未收敛时返回**显式缺失**，而非抛异常或用零冒充解——一个被当成 0 的
    隐含波动率会让下游选出完全错误的合约，且不会报任何错。
    """
    if t <= _EPS:
        return Maybe.missing("已到期，无法反解隐含波动率")
    if price <= 0:
        return Maybe.missing("价格非正")

    low, high = price_bounds(s, k, t, r, q, right)
    if price < low - 1e-9:
        return Maybe.missing("价格低于内在价值下界")
    if price > high + 1e-9:
        return Maybe.missing("价格高于理论上界")

    def objective(sigma: float) -> float:
        return float(bs_price(s, k, t, r, sigma, q, right)) - price

    lo, hi = IV_BOUNDS
    f_lo, f_hi = objective(lo), objective(hi)
    if f_lo > 0 or f_hi < 0:
        return Maybe.missing("目标价格落在可搜索波动率区间之外")
    try:
        root = brentq(objective, lo, hi, xtol=1e-10, rtol=1e-12, maxiter=200)
    except (ValueError, RuntimeError):
        return Maybe.missing("数值反解未收敛")
    return Maybe.some(float(root))


def has_early_exercise_value(
    *,
    right: Right,
    option_price: float,
    intrinsic: float,
    dividend_in_window: float,
) -> bool:
    """判断深度实值看涨在除息前是否存在提前行权价值。

    欧式模型用于美式期权是**显式近似**。对虚值与平值期权误差在千分之几量级，
    但实值看涨在除息日前、时间价值低于该次分红时，欧式价确实是错的。这里不改模型，
    只把这种情形标出来，使它不会被默认当作公允价值。
    """
    if right is not Right.CALL or dividend_in_window <= 0:
        return False
    time_value = option_price - intrinsic
    return intrinsic > 0 and time_value < dividend_in_window
