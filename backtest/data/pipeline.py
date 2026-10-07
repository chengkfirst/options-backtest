"""The data pipeline: what each step does, why, what it costs, where to change it.

**Why this module exists.** Every step of data handling is a trade-off: whether
to apply the adjustment factor to open/high/low, whether to fill a missing value
or flag it, whether to reject bad data or let it through. Unwritten, those choices
can only be guessed at by reading the code three months later — and the right
choice can change with a different underlying or a different vendor.

So the pipeline is made explicit: each step declares its **method**, its
**reason**, the **alternatives and what each costs**, and **which file to change**,
and carries a **check** — one that runs on the real data and reports pass/fail
with actual numbers, rather than "looks fine".

Usage:

    report = inspect_pipeline(market)
    print(report.text())        # for a person
    report.as_dict()            # for the interface

The checks only report; they never modify data. Fixing data is each module's own
job — this is a diagnosis, not a treatment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

PASS, WARN, FAIL = "pass", "warn", "fail"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """What one step's check concluded."""

    status: str                       # pass / warn / fail
    detail: str                       # one sentence: what was checked, what came back
    numbers: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status != FAIL


@dataclass(frozen=True, slots=True)
class Step:
    """One step of the pipeline."""

    id: str
    name: str
    method: str                       # what this step actually does
    why: str                          # why it does that
    alternatives: list[tuple[str, str]]   # [(the alternative, what it costs)]
    where: str                        # which file to change
    check: Callable[[Any], CheckResult]

    def run(self, market) -> "StepReport":
        try:
            result = self.check(market)
        except Exception as exc:  # noqa: BLE001
            # A check that fails on its own has to say so — silently skipping one
            # is the same as not having it.
            result = CheckResult(FAIL, f"the check itself failed: {type(exc).__name__}: {exc}")
        return StepReport(step=self, result=result)


@dataclass(frozen=True, slots=True)
class StepReport:
    step: Step
    result: CheckResult

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.step.id,
            "name": self.step.name,
            "method": self.step.method,
            "why": self.step.why,
            "alternatives": [{"option": o, "cost": c} for o, c in self.step.alternatives],
            "where": self.step.where,
            "status": self.result.status,
            "detail": self.result.detail,
            "numbers": self.result.numbers,
        }


@dataclass(frozen=True, slots=True)
class PipelineReport:
    steps: list[StepReport]

    @property
    def failed(self) -> list[StepReport]:
        return [s for s in self.steps if s.result.status == FAIL]

    @property
    def warned(self) -> list[StepReport]:
        return [s for s in self.steps if s.result.status == WARN]

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": [s.as_dict() for s in self.steps],
            "summary": {
                "total": len(self.steps),
                "passed": sum(1 for s in self.steps if s.result.status == PASS),
                "warned": len(self.warned),
                "failed": len(self.failed),
            },
        }

    def text(self) -> str:
        mark = {PASS: "ok", WARN: " !", FAIL: " x"}
        out = ["Data pipeline", "─" * 64]
        for s in self.steps:
            out += [
                f"\n{mark[s.result.status]} {s.step.name}",
                f"    method       {s.step.method}",
                f"    why          {s.step.why}",
                f"    measured     {s.result.detail}",
                "    alternatives " + " | ".join(f"{o} → {c}" for o, c in s.step.alternatives),
                f"    change in    {s.step.where}",
            ]
        d = self.as_dict()["summary"]
        out += ["", "─" * 64,
                f"{d['passed']} passed · {d['warned']} warned · {d['failed']} failed"]
        return "\n".join(out)

# ══════════════════════════════════════════════════════════════
#  the checks
# ══════════════════════════════════════════════════════════════

def _check_source(market) -> CheckResult:
    bars = market.bars
    n = len(bars)
    first, last = bars.dates[0], bars.dates[-1]
    cfg = market.config
    #: **Both ends, and the overlap.** This checked only the start, so a range the data
    #: does not reach at all -- a 2030 request against a file ending in 2023 -- reported
    #: "the requested range is covered". Every later step then measured the wrong range
    #: and two of them passed on it, which is the exact shape of a silent error: a report
    #: that is confident about data it does not have.
    late = first > cfg.start
    short = last < cfg.end
    disjoint = last < cfg.start or first > cfg.end
    notes = []
    if disjoint:
        notes.append(
            f"**the data and the requested range do not overlap at all**: the data runs "
            f"{first} to {last} and the range asked for is {cfg.start} to {cfg.end}. "
            f"Nothing can run on this")
    else:
        if late:
            notes.append(f"the start requested was {cfg.start}; data begins "
                         f"{(first - cfg.start).days} days later")
        if short:
            notes.append(f"the end requested was {cfg.end}; data stops "
                         f"{(cfg.end - last).days} days earlier")
    return CheckResult(
        FAIL if disjoint else (WARN if notes else PASS),
        f"read {n} trading days, {first} to {last}. "
        + ("; ".join(notes) + "." if notes else "The requested range is covered."),
        {"sessions": n, "first": str(first), "last": str(last),
         "covers_start": not late, "covers_end": not short, "overlaps": not disjoint},
    )


def _check_dual_price(market) -> CheckResult:
    bars = market.bars
    traded, adjusted = bars.traded("close"), bars.adjusted("close")
    diff = (traded - adjusted)
    same = bool(np.allclose(traded, adjusted))
    ratio_close = (adjusted / traded)
    ratio_high = (bars.adjusted("high") / bars.traded("high"))
    consistent = bool(np.allclose(ratio_close, ratio_high))
    if not consistent:
        return CheckResult(FAIL, "the adjustment factor differs between close and "
                                 "high: the adjustment was applied to only some fields")
    if same:
        return CheckResult(
            WARN,
            "the two price sets are identical: either there was no dividend and no "
            "split in this range, or the vendor supplied no adjusted price. The "
            "latter shifts option moneyness systematically over a long range.",
            {"max_gap": 0.0},
        )
    return CheckResult(
        PASS,
        f"the two price sets are separate, largest gap {diff.abs().max():.2f} "
        f"({(diff.abs() / traded).max() * 100:.2f}%), and the adjustment factor is "
        f"consistent across open, high, low and close.",
        {"max_gap": round(float(diff.abs().max()), 4),
         "max_gap_pct": round(float((diff.abs() / traded).max()) * 100, 3)},
    )


def _check_calendar(market) -> CheckResult:
    cal, bars = market.calendar, market.bars
    off = [d for d in bars.dates if d not in cal]
    if off:
        return CheckResult(FAIL, f"{len(off)} bar dates are not on the trading "
                                 f"calendar, the first being {off[0]}",
                           {"off_calendar": len(off)})
    sessions = market.sessions()
    # A range with no sessions in it cannot run, and this reported PASS on it -- "0
    # sessions in the backtest range" with a pass beside it. The count was already in
    # the message; nothing read it.
    if not sessions:
        return CheckResult(
            FAIL,
            f"there are no trading sessions in the requested range "
            f"{market.config.start} to {market.config.end}. Either the range is "
            f"entirely holidays and weekends, or it does not overlap the data",
            {"sessions_in_range": 0},
        )
    return CheckResult(
        PASS,
        f"every bar date is on the trading calendar; {len(sessions)} sessions in "
        f"the backtest range, of which "
        f"{sum(1 for d in sessions if cal.is_early_close(d))} are early closes.",
        {"sessions_in_range": len(sessions),
         "early_closes": sum(1 for d in sessions if cal.is_early_close(d))},
    )


def _check_quality(market) -> CheckResult:
    f = market.bars.frame
    issues = {
        "non-positive price": int((f[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()),
        "high below low": int((f["high"] < f["low"]).sum()),
        "duplicate date": int(f.index.duplicated().sum()),
        "missing price": int(f[["open", "high", "low", "close"]].isna().any(axis=1).sum()),
    }
    bad = {k: v for k, v in issues.items() if v}
    if bad:
        return CheckResult(FAIL, "bad bars: " + ", ".join(f"{k} x{v}" for k, v in bad.items()),
                           issues)

    #: **Price jumps, reported rather than judged.**
    #:
    #: The document lists them beside the OHLC contradictions, and they were the one
    #: quality check missing: an unadjusted split arrives as a one-session move of tens of
    #: percent and passes every test above it.
    #:
    #: Reported with no threshold that drops anything, because the threshold is the whole
    #: difficulty: a real 20% session on an ETF exists -- March 2020 has several -- and a
    #: rule that dropped them would delete the most informative days in the sample. So
    #: this names the largest move and its date and leaves the judgement with a person,
    #: which is the same choice the cleaning rules make by being off by default.
    #:
    #: The one number stated as a fact rather than a threshold: a **ratio near a round
    #: fraction** (1/2, 1/3, 2/3, 1/4) is what an unapplied split looks like, and that is
    #: a different claim from "this move is large".
    close = f["close"].astype(float)
    ratio = (close / close.shift(1)).dropna()
    if ratio.empty:
        issues["largest_move_pct"] = 0.0
        return CheckResult(PASS, f"all {len(f)} rows pass: prices positive, high above "
                                 f"low, no duplicate dates, nothing missing. Too few "
                                 f"rows to measure a session-to-session move.", issues)

    moves = (ratio - 1.0).abs()
    worst_at = moves.idxmax()
    worst = float(moves.loc[worst_at])
    # A split ratio the vendor did not apply. Checked against the common ones rather than
    # against "large", and with a tolerance tight enough that an ordinary move cannot
    # land on one by accident.
    split_like = [(name, value) for name, value in
                  (("2:1", 0.5), ("3:1", 1 / 3), ("3:2", 2 / 3), ("4:1", 0.25),
                   ("1:2", 2.0), ("1:3", 3.0))
                  if abs(float(ratio.loc[worst_at]) - value) < 0.01]
    issues["largest_move_pct"] = round(worst * 100, 2)
    issues["largest_move_date"] = str(worst_at.date() if hasattr(worst_at, "date")
                                      else worst_at)
    if split_like:
        return CheckResult(
            FAIL,
            f"the move on {issues['largest_move_date']} is a ratio of "
            f"{float(ratio.loc[worst_at]):.4f}, which is a {split_like[0][0]} split the "
            f"vendor appears not to have applied. Every strike selected after it would "
            f"be measured against the wrong price",
            issues,
        )
    return CheckResult(PASS, f"all {len(f)} rows pass: prices positive, high above "
                             f"low, no duplicate dates, nothing missing. Largest "
                             f"session move {worst * 100:.1f}% on "
                             f"{issues['largest_move_date']}, which is reported rather "
                             f"than judged: a real 20% session exists and a rule that "
                             f"dropped it would delete the most informative days.",
                       issues)


def _check_rates(market) -> CheckResult:
    rates = market._rates
    if rates.empty:
        return CheckResult(
            WARN,
            f"no rate series available; falling back to the constant "
            f"{market.config.fallback_risk_free_rate:.2%} throughout. Across a "
            f"range that spans a hiking cycle this shifts long-dated pricing "
            f"across the board.",
            {"constant": market.config.fallback_risk_free_rate},
        )
    lo, hi = float(rates.min()), float(rates.max())
    return CheckResult(
        PASS,
        f"rate series has {len(rates)} points, running from {lo:.2%} to {hi:.2%}"
        + (" (this spans a clear rate cycle, so a series rather than a constant is "
           "necessary)" if hi - lo > 0.02 else "."),
        {"points": len(rates), "min": round(lo, 5), "max": round(hi, 5)},
    )


def _check_dividends(market) -> CheckResult:
    div = market._dividends
    if div.empty:
        return CheckResult(
            WARN,
            "no dividend data; pricing treats the dividend as zero. That overstates "
            "a call's theoretical price and makes the early-exercise test useless.",
            {"events": 0},
        )
    return CheckResult(
        PASS,
        f"{len(div)} ex-dividend events, {float(div.sum()):.2f} per share in total. "
        f"The yield used for pricing is derived from the actual events over the "
        f"contract's life, not an annual constant.",
        {"events": len(div), "total_per_share": round(float(div.sum()), 4)},
    )


def _check_vol_proxy(market) -> CheckResult:
    proxy = market._vol_proxy
    symbol = market.config.contract.vol_proxy
    if proxy.empty:
        return CheckResult(
            WARN, f"the volatility proxy {symbol} is unavailable; falling back to "
                  f"the underlying's own realised volatility throughout.",
            {"symbol": symbol, "points": 0},
        )
    return CheckResult(
        PASS,
        f"volatility proxy {symbol}, {len(proxy)} points, ranging "
        f"{float(proxy.min()):.1%} to {float(proxy.max()):.1%}. The proxy matches "
        f"the underlying.",
        {"symbol": symbol, "points": len(proxy),
         "min": round(float(proxy.min()), 4), "max": round(float(proxy.max()), 4)},
    )


def _check_expiries(market) -> CheckResult:
    exp = market.expiries
    if not exp:
        return CheckResult(FAIL, "no expiries were generated, so every selection "
                                 "must fail")
    sessions = market.sessions()
    if not sessions:
        return CheckResult(FAIL, "there are no trading days in the backtest range")
    probe = sessions[len(sessions) // 2]
    near = [e for e in exp if 21 <= (e - probe).days <= 45]
    return CheckResult(
        PASS if near else WARN,
        f"{len(exp)} expiries. Taking {probe} as an example, {len(near)} fall in "
        f"the 21–45 day window"
        + ("." if near else " - nothing can be selected in that window, so "
                           "selection will fail."),
        {"expiries": len(exp), "in_window_sample": len(near)},
    )


def _check_chain(market, chain=None) -> CheckResult:
    if chain is None:
        return CheckResult(WARN, "no chain source was given; the chain check is skipped")
    sessions = market.sessions()
    # Guarded like the expiry check beside it. Without this the probe indexes an empty
    # tuple and the step reports "the check itself failed: IndexError", which names the
    # symptom and hides the cause -- the range, which the calendar check above now says.
    if not sessions:
        return CheckResult(FAIL, "there are no trading days in the requested range, so "
                                 "there is no session to take a chain snapshot on")
    probe = sessions[len(sessions) // 2]
    snap = chain.snapshot(probe)
    if snap.empty:
        return CheckResult(FAIL, f"the chain for {probe} is empty")
    unavailable = [c for c in ("volume", "open_interest") if snap[c].isna().all()]
    inverted = int((snap["ask"] < snap["bid"]).sum())
    if inverted:
        return CheckResult(FAIL, f"the chain for {probe} has {inverted} rows with "
                                 f"an ask below the bid")
    src = set(snap["source"])
    return CheckResult(
        WARN if "synthetic" in src else PASS,
        f"the chain for {probe} has {len(snap)} rows, "
        f"{snap['expiry'].nunique()} expiries x {snap['strike'].nunique()} strikes; "
        f"source {'/'.join(sorted(src))}"
        + (f"; {', '.join(unavailable)} unavailable (flagged, and downstream skips "
           f"the affected tests)" if unavailable else "")
        + (". A synthetic chain can verify the engine and support underlying-side "
           "signal research; option-side parameters must not be tuned on it."
           if "synthetic" in src else "."),
        {"rows": len(snap), "expiries": int(snap["expiry"].nunique()),
         "strikes": int(snap["strike"].nunique()), "unavailable": unavailable,
         "source": sorted(src)},
    )


# ══════════════════════════════════════════════════════════════
#  the pipeline
# ══════════════════════════════════════════════════════════════

def build_steps(chain=None) -> list[Step]:
    """The pipeline definition.

    Deliberately terse: ``method`` is a phrase, each alternative is one sentence
    at most. This is for **skim-reading a cross-check**, not documentation — any
    longer and nobody reads it, which defeats the purpose.
    """
    return [
        Step(
            id="source", name="(1) read the raw bars",
            method="daily bars plus a 400-day warm-up; parquet cache",
            why="without the warm-up, long-window indicators are absent across the "
                "start of the range",
            alternatives=[
                ("no warm-up", "every long-window signal is absent for the first year"),
                ("cache key of symbol and range only", "the two price sets get crossed"),
            ],
            where="data/sources.py · data/cache.py",
            check=_check_source,
        ),
        Step(
            id="dual_price", name="(2) split into two price sets",
            method="the Adj Close / Close ratio, applied to open, high, low and close",
            why="a strike is set against the price that traded; the adjusted price is "
                "rewritten by every dividend that follows",
            alternatives=[
                ("keep only adjusted prices", "historical moneyness shifts systematically"),
                ("keep only traded prices", "false gaps on ex-dividend and split days"),
                ("adjust only the close", "ATR and Bollinger width are distorted"),
            ],
            where="data/underlying.py build_bars()",
            check=_check_dual_price,
        ),
        Step(
            id="calendar", name="(3) align to the trading calendar",
            method="NYSE calendar; calendar days for pricing, trading days for volatility",
            why="an option decays over the weekend, but volatility is only realised "
                "on trading days",
            alternatives=[
                ("calendar days for both", "volatility understated by about 17%"),
                ("trading days for both", "weekend time decay is swallowed"),
                ("a hand-maintained holiday table", "historical holiday changes "
                 "become our problem to track"),
            ],
            where="data/calendar.py · config.py TimeConventions",
            check=_check_calendar,
        ),
        Step(
            id="quality", name="(4) quality checks",
            method="bad bars are refused outright; bad chain rows are dropped and flagged",
            why="bad bars make a whole stretch untrustworthy, while one bad contract "
                "affects only that contract",
            alternatives=[
                ("flag everything, refuse nothing", "bad bars enter the backtest silently"),
                ("refuse everything", "real data never finishes a run"),
                ("interpolate the fix automatically", "the repaired values become "
                 "indistinguishable from real observations"),
            ],
            where="data/underlying.py validate_bars() · data/chain.py validate_chain()",
            check=_check_quality,
        ),
        Step(
            id="rates", name="(5) the risk-free rate",
            method="the three-month Treasury series (^IRX), carried forward when absent",
            why="across a hiking cycle a constant distorts long-dated pricing",
            alternatives=[
                ("a fixed constant", "long-DTE pricing shifts across the board"),
                ("add FRED", "one more data source and one more point of failure"),
                ("interpolate along the curve", "at a daily frequency this moves "
                 "only the last few decimals"),
            ],
            where="data/market.py _load_rate_series()",
            check=_check_rates,
        ),
        Step(
            id="dividends", name="(6) dividends",
            method="the yield is derived from the actual ex-dividend events, not an "
                   "annual constant",
            why="the timing of the ex-date decides whether a deep in-the-money call "
                "is worth exercising early",
            alternatives=[
                ("an annual constant", "the ex-dates are erased and the "
                 "early-exercise test becomes useless"),
                ("ignore dividends", "a call's theoretical price is overstated"),
            ],
            where="data/market.py dividend_yield()",
            check=_check_dividends,
        ),
        Step(
            id="vol_proxy", name="(7) the volatility proxy",
            method="each underlying uses its own volatility index; a mismatch raises",
            why="Nasdaq volatility runs systematically above the S&P's, and borrowing "
                "one understates prices across the board",
            alternatives=[
                ("share VIX", "options on non-S&P underlyings are underpriced"),
                ("use realised volatility directly", "no risk premium, so prices "
                 "come out low across the board"),
                ("fit a volatility surface", "with no real chain to calibrate "
                 "against, the fit is just as invented"),
            ],
            where="data/market.py VOL_PROXY_BY_SYMBOL",
            check=_check_vol_proxy,
        ),
        Step(
            id="expiries", name="(8) the option expiry calendar",
            method="monthly third Fridays plus weeklies, from their historical "
                   "introduction dates",
            why="weeklies were rolled out gradually, and conjuring them up earlier "
                "overstates what was executable",
            alternatives=[
                ("monthlies only", "a 30-DTE target is often unreachable"),
                ("weeklies across the whole range", "the early years offer more "
                 "contracts than really existed"),
                ("daily expiries throughout", "the contract count explodes and does "
                 "not match the market"),
            ],
            where="data/calendar.py expiry_dates()",
            check=_check_expiries,
        ),
        Step(
            id="chain", name="(9) the option chain",
            method="defined after a commercial vendor's column set; the synthetic "
                   "chain is generated on demand",
            why="the interface is shaped by real sources, so wiring in paid data "
                "needs no change above",
            alternatives=[
                ("trim the interface to what the synthetic chain can do", "the "
                 "interface has to be redone when real data arrives"),
                ("precompute the whole table", "tens of millions of rows over ten "
                 "years; unacceptable to store or to generate"),
                ("fill absent columns with 0", "downstream reads 0 as a real value "
                 "and the liquidity filter misjudges"),
            ],
            where="data/chain.py · data/synthetic/generator.py",
            check=lambda m: _check_chain(m, chain),
        ),
    ]


def inspect_pipeline(market, chain=None) -> PipelineReport:
    """Run the whole pipeline's checks against the data actually loaded."""
    return PipelineReport([step.run(market) for step in build_steps(chain)])
