"""Historical event studies: after a fall of a given size, what usually happens.

This is not a backtest. A backtest simulates what trading by a set of rules would
have earned; this asks the question before that one: **does the condition itself
carry information.** It uses only the underlying's price and never touches options —
so its conclusions hold even when the option chain is synthetic.

## Five things that have to be right

1. **Count episodes, not days.** QQQ has spent 1,923 days more than 10% below its
   high, but that is only about 14 independent events. Counting days turns one bear
   market into hundreds of observations and inflates the sample by two orders of
   magnitude.

2. **The unconditional baseline is mandatory.** QQQ's unconditional probability of
   being higher twelve months later is already high. If after a correction it is 80%
   and ordinarily it is 78%, the signal said nothing. **Reporting only the
   conditional probability is self-deception.**

3. **The path and the endpoint are different things.** "Higher twelve months later"
   and "never fell another 10% on the way" are not the same — the second decides
   whether you can hold on, and it is often the real constraint.

4. **The end of the range has to be censored.** An event closer than N months to the
   end of the data has no complete follow-up: it must be excluded, and how many were
   excluded has to be reported, rather than quietly computed over however many days
   exist.

5. **Detect the event on the close, measure the pain intraday.** These are not one
   convention: a dip that recovers by the close is not a correction, but "would I
   have been stopped out" has to read the intraday low. Measured on QQQ: 3,069 days
   broke 5% on a closing basis against 3,529 intraday (15% more); on 2015-08-24 the
   close was down only 3.8% while the intraday low was down 17.2% — any stop was hit,
   and the closing basis cannot see it at all. Using closes throughout understates
   both the drawdown and the probability of a further fall, and all the biases point
   the same way: it looks better than it was.

## The hard reality of sample size

Twenty-seven years of QQQ: about 14 episodes at 10%, 6 at 20%, 3 at 30%. **Three
observations support no statistical conclusion at all** — only "this happened three
times in history, and here is each". This tool says exactly that much, rather than
handing over a percentage that looks precise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

#: Trading days per month. 21 is the convention (252/12).
DAYS_PER_MONTH = 21


def _as_bars(data: "pd.Series | pd.DataFrame") -> tuple[pd.Series, pd.Series, pd.Series, bool]:
    """Reduce the input to (close, intraday high, intraday low, is it really
    intraday).

    A close-only series runs, but **the probability of a further fall and the deepest
    drawdown are then understated systematically** — a fact flagged all the way
    through to the result, rather than left for the user to assume otherwise.
    """
    if isinstance(data, pd.Series):
        c = data.dropna().astype(float)
        return c, c, c, False
    frame = data.copy()
    frame.columns = [str(col).lower() for col in frame.columns]
    if "close" not in frame:
        raise ValueError(f"no close column; only {sorted(frame.columns)}")
    c = frame["close"].astype(float)
    has = {"high", "low"} <= set(frame.columns)
    h = frame["high"].astype(float) if has else c
    l = frame["low"].astype(float) if has else c
    keep = c.notna()
    return c[keep], h[keep], l[keep], has


@dataclass(frozen=True, slots=True)
class Episode:
    """One correction episode, from the moment the threshold is first crossed."""

    crossed: pd.Timestamp        # the date the threshold was first crossed
    price: float                 # the price at the crossing; every return is from here
    ref_high: float              # the reference high before the event
    ref_high_date: pd.Timestamp
    drawdown: float              # the actual drawdown on the crossing day (negative)
    ended: pd.Timestamp | None   # when the episode ended (recovered to the reset line)


def find_corrections(
    data: "pd.Series | pd.DataFrame",
    threshold: float,
    *,
    reference_window: int = 252,
    reset_fraction: float = 0.5,
) -> list[Episode]:
    """Find every independent episode that crossed ``threshold``.

    ``threshold`` is positive: 0.10 means a 10% correction.

    The **reference high** is the rolling ``reference_window``-day closing high
    (252 ≈ 52 weeks by default). A rolling window rather than the all-time high: the
    latter would leave the decade after 2000 permanently "in a correction", merging
    periods of quite different character into one.

    **What makes an episode**: one correction = **the stretch measured from one prior
    high**. Counting again requires a new reference high — August and September 2024
    each touched a 10% correction against the same 503 high, which is two touches
    within one correction, not two independent events. Resetting on "recovered to some
    level" would count it as two and inflate the sample.

    ``reset_fraction`` only decides which day goes into ``ended`` (the day the drawdown
    recovers to ``-threshold * reset_fraction``); it does not affect the count.
    """
    if not 0 < threshold < 1:
        raise ValueError(f"threshold is the positive size of the correction, 0.10 for "
                         f"10%, got {threshold}")
    if not 0 <= reset_fraction <= 1:
        raise ValueError(f"reset_fraction must lie between 0 and 1, got {reset_fraction}")

    # **Events are always detected on the close**: a dip that recovers by the close is
    # not a correction.
    prices, _, _, _ = _as_bars(data)
    ref = prices.rolling(reference_window, min_periods=reference_window // 4).max()
    ref_idx = prices.rolling(reference_window, min_periods=reference_window // 4).apply(
        lambda w: float(np.argmax(w)) - len(w) + 1, raw=True
    )
    dd = (prices / ref - 1.0)

    episodes: list[Episode] = []
    open_at: int | None = None
    reset_level = -threshold * reset_fraction
    last_peak: int | None = None             # where the last episode's reference high was

    values = dd.to_numpy()
    for i in range(len(prices)):
        if np.isnan(values[i]):
            continue
        if open_at is None:
            # A new episode requires **a different reference high** (a later date),
            # not a new all-time high. Requiring a new high would swallow every
            # correction in the sixteen years after the 2000 crash.
            fresh_peak = True
            if values[i] <= -threshold:
                offset0 = ref_idx.iloc[i]
                peak0 = i + int(offset0) if np.isfinite(offset0) else i
                fresh_peak = last_peak is None or peak0 > last_peak
            if values[i] <= -threshold and fresh_peak:
                offset = ref_idx.iloc[i]
                hi_pos = i + int(offset) if np.isfinite(offset) else i
                hi_pos = max(0, min(hi_pos, len(prices) - 1))
                episodes.append(Episode(
                    crossed=prices.index[i], price=float(prices.iloc[i]),
                    ref_high=float(ref.iloc[i]), ref_high_date=prices.index[hi_pos],
                    drawdown=float(values[i]), ended=None,
                ))
                open_at = i
                last_peak = hi_pos
        elif values[i] >= reset_level:
            last = episodes[-1]
            episodes[-1] = Episode(last.crossed, last.price, last.ref_high,
                                   last.ref_high_date, last.drawdown, prices.index[i])
            open_at = None
    return episodes


@dataclass(frozen=True, slots=True)
class Outcome:
    """What happened within one horizon after one correction."""

    crossed: pd.Timestamp
    horizon_months: int
    end_return: float              # the return at the end of the horizon, from the crossing
    finished_up: bool
    finished_above_5: bool
    finished_above_10: bool
    recovered_pre_high: bool       # back to the prior high **at any point** in the window
    days_to_recover: float         # trading days to the prior high; nan if never
    touched_minus_5: bool          # fell another 5% **at any point intraday** in the window
    touched_minus_10: bool
    max_drawdown_after: float      # deepest fall from the crossing in the window (intraday)
    #: Whether the two "touched" flags and the deepest drawdown used real intraday
    #: data. When false they are **understated systematically**.
    intraday: bool = True


def measure(
    data: "pd.Series | pd.DataFrame",
    episodes: Sequence[Episode],
    *,
    horizons_months: Sequence[int] = (1, 3, 6, 12),
) -> pd.DataFrame:
    """Each correction's outcome in each horizon.

    **Censoring at the end**: an episode closer than N months to the end of the data
    simply **does not appear** for that horizon, rather than being computed over
    however many days exist — making do that way biases systematically towards short
    windows that have not had time to fall. How many were excluded is reported by
    :func:`study`.
    """
    prices, highs, lows, intraday = _as_bars(data)
    rows: list[Outcome] = []
    for ep in episodes:
        try:
            i0 = prices.index.get_loc(ep.crossed)
        except KeyError:
            continue
        for months in horizons_months:
            n = months * DAYS_PER_MONTH
            if i0 + n >= len(prices):
                continue                      # not enough data: this horizon does not count
            path = prices.iloc[i0: i0 + n + 1]
            rel = path / ep.price - 1.0
            # **Measure the pain on the intraday low**: a stop is hit by an intraday
            # touch, not by a close.
            worst = lows.iloc[i0: i0 + n + 1] / ep.price - 1.0
            # **Recovery is judged on the close**: touching the prior high is not it.
            recovered = path >= ep.ref_high
            first = int(np.argmax(recovered.to_numpy())) if recovered.any() else -1
            rows.append(Outcome(
                crossed=ep.crossed, horizon_months=months,
                end_return=float(rel.iloc[-1]),
                finished_up=bool(rel.iloc[-1] > 0),
                finished_above_5=bool(rel.iloc[-1] >= 0.05),
                finished_above_10=bool(rel.iloc[-1] >= 0.10),
                recovered_pre_high=bool(recovered.any()),
                days_to_recover=float(first) if first >= 0 else float("nan"),
                touched_minus_5=bool((worst <= -0.05).any()),
                touched_minus_10=bool((worst <= -0.10).any()),
                max_drawdown_after=float(worst.min()),
                intraday=intraday,
            ))
    # A slots dataclass has no __dict__, so use asdict.
    from dataclasses import asdict

    return pd.DataFrame([asdict(r) for r in rows]) if rows else pd.DataFrame()


def baseline(
    data: "pd.Series | pd.DataFrame",
    *,
    horizons_months: Sequence[int] = (1, 3, 6, 12),
) -> pd.DataFrame:
    """**The unconditional baseline**: enter on any day at random, same horizons.

    This is the step most easily skipped and least skippable in the whole study. QQQ's
    unconditional probability of being higher twelve months later is already high; if
    after a correction it is 80% and ordinarily 78%, the signal said nothing. Reporting
    only the conditional probability is self-deception.
    """
    prices, _, _, _ = _as_bars(data)
    rows = []
    for months in horizons_months:
        n = months * DAYS_PER_MONTH
        if n >= len(prices):
            continue
        start = prices.iloc[:-n].to_numpy()
        end = prices.iloc[n:].to_numpy()
        rel = end / start - 1.0
        rows.append({
            "horizon_months": months,
            "sample": len(rel),
            "up": float((rel > 0).mean()),
            "up 5%+": float((rel >= 0.05).mean()),
            "up 10%+": float((rel >= 0.10).mean()),
            "median return": float(np.median(rel)),
        })
    return pd.DataFrame(rows)


def effective_sample(episodes: Sequence[Episode], months: int) -> int:
    """How many episodes do not overlap.

    Twelve-month windows overlap heavily in time, which inflates the nominal sample
    badly — the twelve months after April 2000 and after October 2000 are nearly the
    same stretch of history, and they are not two independent observations. What is
    counted here is the largest subset of **non-overlapping windows** (greedy, in time
    order).
    """
    if not episodes:
        return 0
    span = pd.Timedelta(days=months * 30)
    count, last_end = 0, None
    for ep in sorted(episodes, key=lambda e: e.crossed):
        if last_end is None or ep.crossed >= last_end:
            count += 1
            last_end = ep.crossed + span
    return count


def study(
    data: "pd.Series | pd.DataFrame",
    threshold: float,
    *,
    horizons_months: Sequence[int] = (1, 3, 6, 12),
    reference_window: int = 252,
) -> dict:
    """A complete event study: find the episodes, measure the outcomes, compare
    against the baseline, report the sample size.

    Each row of the returned ``table`` is one horizon, with the conditional probability
    beside the unconditional baseline and the **excess** between them (conditional
    minus baseline). The excess is the signal's content; the conditional probability by
    itself is not.
    """
    prices, _, _, intraday = _as_bars(data)
    eps = find_corrections(data, threshold, reference_window=reference_window)
    out = measure(data, eps, horizons_months=horizons_months)
    base = baseline(data, horizons_months=horizons_months).set_index("horizon_months")

    rows = []
    for months in horizons_months:
        sub = out[out["horizon_months"] == months] if len(out) else out
        n = len(sub)
        eff = effective_sample(
            [e for e in eps if e.crossed in set(sub["crossed"])] if n else [], months)
        if months not in base.index or n == 0:
            rows.append({"horizon": f"{months}m", "sample": n, "effective": eff,
                         "note": "not enough data; no observations for this horizon"})
            continue
        b = base.loc[months]
        rows.append({
            "horizon": f"{months}m",
            "sample": n, "effective": eff,
            "up": float(sub["finished_up"].mean()),
            "baseline up": float(b["up"]),
            "excess": float(sub["finished_up"].mean() - b["up"]),
            "up 5%+": float(sub["finished_above_5"].mean()),
            "recovered prior high": float(sub["recovered_pre_high"].mean()),
            "fell another 5%": float(sub["touched_minus_5"].mean()),
            "fell another 10%": float(sub["touched_minus_10"].mean()),
            "median return": float(sub["end_return"].median()),
            "median deepest drawdown": float(sub["max_drawdown_after"].median()),
            "median days to recover": float(sub["days_to_recover"].median()),
            "note": _sample_warning(n, eff),
        })
    return {
        "threshold": threshold,
        # Whether real intraday data was given. When false, the "fell another X%"
        # flags and the deepest drawdown are understated systematically — measured at
        # about 12-15% on QQQ, and by tens of percentage points on extreme days.
        "intraday": intraday,
        "basis": ("events detected on the close; further falls and the deepest "
                  "drawdown measured on the intraday low" if intraday
                  else "**only a close series was given**: further falls and the "
                       "deepest drawdown are understated; pass OHLC for the real "
                       "figures"),
        "episodes": eps,
        "outcomes": out,
        "baseline": base.reset_index(),
        "table": pd.DataFrame(rows),
        "excluded_by_data_end": {
            m: len(eps) - int((out["horizon_months"] == m).sum()) if len(out) else len(eps)
            for m in horizons_months
        },
    }


def _sample_warning(n: int, eff: int) -> str:
    """Say exactly as much as the sample size supports, rather than handing over a
    percentage that looks precise."""
    if eff <= 3:
        return (f"**only {eff} non-overlapping observations — this is not statistics, "
                f"it is {eff} stories**")
    if eff <= 8:
        return f"effective sample {eff}: read the direction, not the decimals"
    if n > eff * 2:
        return (f"{n} episodes nominally but only {eff} non-overlapping, so the "
                f"confidence interval on these probabilities is far wider than it looks")
    return ""
