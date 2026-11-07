"""Wiring in **your own** option data.

This module exists for one reason: to make replacing the synthetic chain a
**mechanical, verifiable** act rather than an archaeological dig through code.

The synthetic chain is a **test fixture**, not a data source. Its only use is
getting the whole pipeline to run when there is no real data. No option-side
conclusion (which delta to pick, where to set a take-profit, how to use skew)
holds on it — its IV is a constant, with no skew and no term structure.

## Switching to your data, in three steps

1. **See the format**: ``describe_schema()`` prints which columns are needed,
   what each one means, and whether it may be absent.
2. **Check the data**: ``check_file(path)`` tells you what is wrong with it
   before any backtest runs.
3. **Wire it in**: ``run(..., chain="file:your/path")``, or
   ``run(..., chain=FileChainSource(path, market, config))``.

The layers above (selection, engine, research) need **no change at all** — they
know only the ``ChainSource`` protocol.

## When the column names do not match

Do not edit your file. Pass ``column_map`` a ``{your name: standard name}``:

    FileChainSource(path, market, config,
                    column_map={"trade_date": "asof", "impl_vol": "iv"})

The mapping happens once on read; from then on the system sees only standard
column names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from backtest.core.config import CALENDAR_DAYS_PER_YEAR, BacktestConfig
from backtest.data.options.chain import empty_chain, validate_chain
from backtest.core.dataview import LookAheadError
from backtest.core.errors import DataQualityError, StartupError
from backtest.core.types import CHAIN_COLUMNS, ContractId, Right

#: What each column means and whether it may be absent. A missing ``required``
#: column is refused outright; the rest are recorded as absent, with the
#: consequence stated.
SCHEMA: dict[str, dict[str, str]] = {
    "asof":             {"required": "yes", "means": "quote date (which day this row is a snapshot of)", "if absent": "—"},
    "symbol":           {"required": "yes", "means": "underlying ticker, e.g. QQQ", "if absent": "—"},
    "expiry":           {"required": "yes", "means": "expiry date", "if absent": "—"},
    "strike":           {"required": "yes", "means": "strike price", "if absent": "—"},
    "right":            {"required": "yes", "means": "C or P (call/put and CALL/PUT also accepted)", "if absent": "—"},
    "bid":              {"required": "yes", "means": "best bid", "if absent": "—"},
    "ask":              {"required": "yes", "means": "best ask", "if absent": "—"},
    "underlying_price": {"required": "yes", "means": "spot at that moment (**the traded price, not the adjusted one**)",
                         "if absent": "—"},
    "available_at":     {"required": "no",
                         "means": "the date this row could first have been **known** — not "
                                  "the date it describes. The two differ when a vendor "
                                  "delivers late, revises, or backfills",
                         "if absent": "assumed equal to asof, and the run records that it "
                                      "was assumed rather than supplied"},
    "last":             {"required": "no", "means": "last traded price", "if absent": "no effect; fills are derived from bid/ask"},
    "volume":           {"required": "no", "means": "contracts traded that day", "if absent": "the volume filter is skipped and flagged"},
    "open_interest":    {"required": "no", "means": "open contracts", "if absent": "the open-interest filter is skipped and flagged"},
    "iv":               {"required": "no", "means": "implied volatility (a decimal: 0.25, not 25)",
                         "if absent": "**solved from the bid/ask mid**; rows that fail are marked absent"},
    "delta":            {"required": "no", "means": "", "if absent": "**derived from IV with Black-Scholes**, and flagged as such"},
    "gamma":            {"required": "no", "means": "", "if absent": "as above"},
    "vega":             {"required": "no", "means": "", "if absent": "as above"},
    "theta":            {"required": "no", "means": "time decay per **calendar day** (negative)", "if absent": "as above"},
}

#: The common spellings of the right column -> the standard value.
_RIGHT_ALIASES: dict[str, str] = {
    "c": "C", "call": "C", "calls": "C", "1": "C",
    "p": "P", "put": "P", "puts": "P", "-1": "P", "0": "P",
}


def describe_schema() -> pd.DataFrame:
    """Print which columns are needed. Read this table before wiring in data."""
    return pd.DataFrame(SCHEMA).T.rename_axis("column")


@dataclass(frozen=True, slots=True)
class FileReport:
    """A health report on one dataset. When ``ok`` is false, do **not** run a
    backtest on it."""

    ok: bool
    path: str
    rows: int
    days: int
    contracts: int
    date_range: tuple[date, date] | None
    errors: tuple[str, ...]        # fatal: unusable until fixed
    warnings: tuple[str, ...]      # usable, but the conclusions are weaker
    columns_present: tuple[str, ...]
    columns_missing: tuple[str, ...]

    def summary(self, printout: bool = True) -> str:
        head = "PASS" if self.ok else "**FAIL**"
        lines = [
            f"Data health check: {head}",
            f"  file      {self.path}",
            f"  size      {self.rows:,} rows / {self.days:,} trading days / "
            f"{self.contracts:,} contracts",
        ]
        if self.date_range:
            lines.append(f"  range     {self.date_range[0]} → {self.date_range[1]}")
        if self.columns_missing:
            lines.append(f"  absent    {', '.join(self.columns_missing)}")
        if self.errors:
            lines += ["", "Fatal (unusable until fixed):"] + [f"  x {e}" for e in self.errors]
        if self.warnings:
            lines += ["", "Warnings (usable, but be aware):"] + [f"  ! {w}" for w in self.warnings]
        if not self.errors and not self.warnings:
            lines += ["", "Nothing found."]
        text = "\n".join(lines)
        if printout:
            print(text)
        return text


# ── reading and normalisation ───────────────────────────────

def _read_any(path: Path) -> pd.DataFrame:
    """Read parquet / csv / a directory of either. The extension decides the
    format; nothing is guessed."""
    if path.is_dir():
        files = sorted([*path.glob("*.parquet"), *path.glob("*.csv")])
        if not files:
            raise StartupError(f"no .parquet or .csv in the directory: {path}")
        return pd.concat([_read_any(f) for f in files], ignore_index=True)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    if path.suffix in (".csv", ".txt"):
        return pd.read_csv(path)
    raise StartupError(f"unrecognised file type {path.suffix}: only .parquet and "
                       ".csv are supported (or a directory of them)")


def normalise(
    frame: pd.DataFrame,
    column_map: Mapping[str, str] | None = None,
    scale: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """Normalise a frame from any source to the standard columns and values.

    Only **lossless** transformations: rename columns, rescale units per
    ``scale``, unify the spelling of ``right``, parse date columns. Nothing is
    filled in — filling in values is data-quality work, which has to be seen and
    decided on, not done quietly on read.

    ``scale`` converts units; the common case is IV stored as a percentage:
    ``{"iv": 0.01}``. **You are deliberately made to say so, rather than having
    anything over some threshold divided by 100** — an automatic conversion turns
    a mislabelled column (another series passed off as iv) into a wrong result
    that looks right. The health check tells you whether you need it.
    """
    out = frame.rename(columns=dict(column_map or {}))
    out.columns = [str(c).strip().lower() for c in out.columns]

    if "right" in out.columns:
        # A numeric column read back as float gives "1.0" and "-1.0", which no alias
        # matches -- and a parquet column of 1/-1 becomes float the moment it holds a
        # single null, or depending on which writer produced it. The trailing ".0" is
        # dropped before matching so the same file does not load on one machine and fail
        # on another.
        raw = (out["right"].astype(str).str.strip().str.lower()
               .str.replace(r"\.0+$", "", regex=True))
        mapped = raw.map(_RIGHT_ALIASES)
        unknown = sorted(set(raw[mapped.isna()]))
        if unknown:
            raise DataQualityError(
                f"unrecognised values in the right column: {unknown[:8]}. "
                f"Accepted spellings: {sorted(set(_RIGHT_ALIASES))}"
            )
        out["right"] = mapped

    for col, factor in (scale or {}).items():
        if col in out.columns:
            out[col] = out[col].astype(float) * float(factor)

    for col in ("asof", "expiry", "available_at"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.date
    return out


# ── the health check ────────────────────────────────────────

def check_frame(frame: pd.DataFrame, *, path: str = "(in-memory frame)") -> FileReport:
    """Look at the data for problems **before** any backtest runs.

    Two tiers: ``errors`` make it unusable until fixed; ``warnings`` leave it
    usable but weaken the conclusions. The distinction matters — treating every
    problem as fatal just pushes people into turning the check off.
    """
    errors: list[str] = []
    warnings: list[str] = []

    required = [c for c, m in SCHEMA.items() if m["required"] == "yes"]
    present = [c for c in SCHEMA if c in frame.columns]
    missing = [c for c in SCHEMA if c not in frame.columns]
    for col in required:
        if col not in frame.columns:
            errors.append(f"required column {col} is absent ({SCHEMA[col]['means']})")
    for col in missing:
        if col not in required:
            warnings.append(f"no {col} column -> {SCHEMA[col]['if absent']}")

    if errors or frame.empty:
        return FileReport(
            ok=False, path=path, rows=len(frame), days=0, contracts=0, date_range=None,
            errors=tuple(errors or ("the frame is empty",)), warnings=tuple(warnings),
            columns_present=tuple(present), columns_missing=tuple(missing),
        )

    days = sorted({d for d in frame["asof"].dropna().unique()})
    contracts = frame.drop_duplicates(subset=["symbol", "expiry", "strike", "right"])

    if frame["asof"].isna().any():
        errors.append(f"{int(frame['asof'].isna().sum())} rows have an unparseable asof")
    if frame["expiry"].isna().any():
        errors.append(f"{int(frame['expiry'].isna().sum())} rows have an unparseable expiry")

    dup = frame.duplicated(subset=["asof", "symbol", "expiry", "strike", "right"])
    if dup.any():
        errors.append(f"{int(dup.sum())} rows share a primary key "
                      "(the same contract appears more than once on one day)")

    bid, ask = frame["bid"].astype(float), frame["ask"].astype(float)
    crossed = (ask < bid) & bid.notna() & ask.notna()
    if crossed.any():
        errors.append(f"{int(crossed.sum())} rows have ask < bid (a crossed quote)")
    nonpos = (ask <= 0) | (bid < 0)
    if nonpos.any():
        warnings.append(f"{int(nonpos.sum())} rows have a non-positive quote and "
                        "will be dropped")

    expired = pd.Series([e < a for a, e in zip(frame["asof"], frame["expiry"])])
    if expired.any():
        warnings.append(f"{int(expired.sum())} rows expire before their quote date "
                        "and will be dropped")

    if "iv" in frame.columns:
        iv = frame["iv"].dropna().astype(float)
        if len(iv) and iv.max() > 5:
            warnings.append(f"iv tops out at {iv.max():.1f}, which looks like a "
                            "percentage rather than a decimal (0.25, not 25)")
        if len(iv) and iv.nunique() <= 2:
            warnings.append(f"iv takes only {iv.nunique()} distinct values - no skew "
                            "and no term structure; option-side conclusions do not "
                            "hold on data like this")

    # **A price below intrinsic value is arbitrage, which means it is bad data.**
    #
    # The document lists it among the validation checks and it was missing. It is the
    # commonest real defect in an option feed -- a stale or crossed quote on a deep
    # in-the-money contract -- and it is **silent**: the price looks ordinary, the implied
    # vol solves to something plausible-looking or fails quietly, and a backtest that buys
    # there books a profit that could not have been taken.
    #
    # A warning rather than fatal, and the share is what matters: a handful of rows is a
    # feed hiccup, and a tenth of the file is a systematic problem with how it was built.
    if {"underlying_price", "strike", "right"} <= set(frame.columns):
        spot = frame["underlying_price"].astype(float)
        strike = frame["strike"].astype(float)
        is_call = frame["right"].astype(str).str.upper().str[0] == "C"
        intrinsic = np.where(is_call, (spot - strike).clip(lower=0),
                             (strike - spot).clip(lower=0))
        # Against the ask: a price you could have *paid* below intrinsic is the arbitrage.
        # The bid being below it is ordinary -- nobody is obliged to bid fair value.
        below = frame["ask"].astype(float) < intrinsic - 0.005
        if below.any():
            share = float(below.mean())
            warnings.append(
                f"{int(below.sum())} rows ({share:.1%}) are offered below their intrinsic "
                f"value, which is arbitrage and therefore bad data. A backtest buying "
                f"there books a profit that could not have been taken"
            )

    # A locked market: bid equal to ask. Not crossed, so the crossed check misses it, and
    # not tradable either -- a real quote has a spread, and a zero-width one is a feed
    # artefact or a halt. Flagged separately because the fill model would happily use the
    # mid and report a cost of zero.
    locked = (frame["bid"].astype(float) == frame["ask"].astype(float)) & \
             (frame["ask"].astype(float) > 0)
    if locked.any():
        warnings.append(f"{int(locked.sum())} rows have bid equal to ask (a locked "
                        f"market), so a fill there would be modelled at no cost")

    # Stale quotes: the same contract's mid unchanged over consecutive days.
    stale = _stale_share(frame)
    if stale > 0.20:
        warnings.append(f"{stale:.0%} of quotes are identical to the previous day "
                        "(stale quotes); take-profit and stop triggers will fire late")

    gaps = _missing_day_share(days)
    if gaps > 0.05:
        warnings.append(f"about {gaps:.0%} of trading days are missing - there are "
                        "stretches with no quotes at all")

    return FileReport(
        ok=not errors, path=path, rows=len(frame), days=len(days), contracts=len(contracts),
        date_range=(days[0], days[-1]) if days else None,
        errors=tuple(errors), warnings=tuple(warnings),
        columns_present=tuple(present), columns_missing=tuple(missing),
    )


def check_file(path: str | Path, column_map: Mapping[str, str] | None = None,
               scale: Mapping[str, float] | None = None) -> FileReport:
    """Read a file (or directory) and check it. This is step two of wiring in
    data."""
    path = Path(path)
    frame = normalise(_read_any(path), column_map, scale)
    return check_frame(frame, path=str(path))


def _stale_share(frame: pd.DataFrame) -> float:
    if not {"bid", "ask"} <= set(frame.columns):
        return 0.0
    mid = (frame["bid"].astype(float) + frame["ask"].astype(float)) / 2.0
    key = list(zip(frame["symbol"], frame["expiry"], frame["strike"], frame["right"]))
    tmp = pd.DataFrame({"k": key, "asof": frame["asof"], "mid": mid}).sort_values(["k", "asof"])
    same = tmp.groupby("k", sort=False)["mid"].diff().eq(0.0)
    return float(same.mean()) if len(same) else 0.0


def _missing_day_share(days: list) -> float:
    """What fraction of weekdays in the range have no data at all. A rough
    measure; it does not consult the trading calendar."""
    if len(days) < 2:
        return 0.0
    span = pd.bdate_range(days[0], days[-1])
    return 1.0 - len(days) / max(len(span), 1)


# ── the data source ─────────────────────────────────────────

class FileChainSource:
    """Read an option chain from your files. Implements the ``ChainSource``
    protocol, so it is directly interchangeable with the synthetic chain.

    Absent optional columns are **filled in and flagged**:

    - no ``iv``: solved from the mid **at the market's own dividend yield**, with a
      European model; rows that fail are marked absent, not 0
    - no greeks: derived from IV with Black-Scholes, and
      ``greeks_model_derived`` is set

    That flag matters — a vendor's delta and a delta I derived are not the same
    thing, and conclusions drawn on them do not deserve the same confidence. So
    the flag travels all the way into the trade records rather than being
    flattened here.
    """

    def __init__(
        self,
        path: str | Path,
        market,
        config: BacktestConfig | None = None,
        *,
        column_map: Mapping[str, str] | None = None,
        scale: Mapping[str, float] | None = None,
        strict: bool = True,
        cleaning=None,
        iv_model: str = "european",
    ) -> None:
        if iv_model not in ("european", "american"):
            raise StartupError(
                f"iv_model={iv_model!r}; one of 'european' or 'american'. European is "
                f"the default and is biased **low** where early exercise has value; "
                f"American is accurate and costs about 13 ms a contract, which is 43 "
                f"minutes on 200,000 rows"
            )
        self.iv_model = iv_model
        self.path = Path(path)
        self.market = market
        self.config = config if config is not None else market.config
        frame = normalise(_read_any(self.path), column_map, scale)
        report = check_frame(frame, path=str(self.path))
        if strict and not report.ok:
            raise DataQualityError(
                "the data failed its health check; refusing to run on it:\n"
                + report.summary(printout=False)
            )
        self.report = report
        # **Cleaning runs after validation and before anything derived.** After, because
        # a rule needs the quality report's own findings to be worth writing; before,
        # because an IV solved from a crossed quote is a number produced from data the
        # researcher had decided to remove.
        #
        # `None` means no rules, which is what every run did before this existed and is
        # why adding it moves no result. See `data/options/clean.py` for why there is no
        # default set.
        from backtest.data.options.clean import Cleaning

        self.cleaning = cleaning if cleaning is not None else Cleaning()
        frame, self.cleaning_report = self.cleaning.apply(frame)
        #: Whether the availability dates were supplied or assumed. Set by `_prepare`,
        #: read by provenance: a run whose look-ahead protection rests on an assumption
        #: should say so rather than read the same as one where the data carried it.
        self.availability_assumed = False
        #: How many rows were withheld for not having been known yet. Counted rather than
        #: silently dropped -- a feed that routinely arrives a day late should show up as
        #: a number, not as slightly fewer trades.
        self._withheld = 0
        self._frame = self._prepare(frame)
        self._by_day: dict[date, pd.DataFrame] = {
            d: g.reset_index(drop=True) for d, g in self._frame.groupby("asof", sort=False)
        }
        #: ``(symbol, expiry, right, strike) -> that contract's rows, indexed by asof``.
        #:
        #: **Built once, because ``series`` is the hot path.** It is called once per open
        #: position per session -- for marking, for excursions, for the shadow window --
        #: and the first version filtered the whole table with five boolean masks each
        #: time. On 626,000 rows over 120 sessions that is minutes; a ten-minute timeout
        #: on a single six-month backtest is what found it.
        #:
        #: Grouping rather than a sorted multi-index: the lookup is exact on all four
        #: fields and never a range, so a dict of small frames is both faster and simpler
        #: than an index that would also have to be kept sorted.
        self._by_contract: dict[tuple, pd.DataFrame] = {}
        known = self._frame
        if "available_at" in known.columns:
            # The same cut as `snapshot` and for the same reason, applied once here
            # rather than on every call: a quote delivered after the day it describes is
            # not available on that day, and marking a position against it would be
            # look-ahead in the valuation rather than in the signal -- quieter, and just
            # as wrong.
            known = known.loc[known["available_at"] <= known["asof"]]
        for key, group in known.groupby(
                ["symbol", "expiry", "right", "strike"], sort=False):
            self._by_contract[(key[0], key[1], key[2], float(key[3]))] = \
                group.set_index("asof", drop=False)

    # ---- the protocol ----
    def snapshot(self, day: date, *, known_by: date | None = None) -> pd.DataFrame:
        """Every contract quoted on ``day`` that was known by ``known_by``.

        Two different questions share this method, and conflating them is how a late feed
        turns into silent look-ahead or into silently missing history:

        - **what can be traded now**: ``known_by`` is ``day``, the default. A quote the
          vendor had not delivered yet cannot be acted on, so it is withheld.
        - **what is known now about then**: ``known_by`` is today. A quote for last
          Tuesday that arrived on Thursday is perfectly good history by Friday, and a
          signal computed on Friday may use it.

        The trading path takes the default, which is why it was possible to write this
        method with one date and not notice. Research reading history has to say when it
        is asking from, or it either sees data that had not arrived or never sees data
        that arrived late.
        """
        got = self._by_day.get(day)
        if got is None:
            return empty_chain()
        if "available_at" not in got.columns:
            return got
        cutoff = known_by or day
        if cutoff < day:
            raise LookAheadError(
                f"asked what was known by {cutoff} about {day}, which is later. A view "
                f"cannot read a day that has not happened"
            )
        known = got.loc[got["available_at"] <= cutoff]
        if len(known) < len(got):
            self._withheld += len(got) - len(known)
        return known.reset_index(drop=True) if len(known) else empty_chain()

    def series(self, contract: ContractId, days: Iterable[date]) -> pd.DataFrame:
        """One contract's rows on the given days.

        A dict lookup on the contract and then a reindex on the days, rather than five
        boolean masks over the whole table -- see ``_by_contract`` for why.
        """
        group = self._by_contract.get(
            (contract.symbol, contract.expiry, contract.right.value,
             float(contract.strike)))
        if group is None:
            return empty_chain()
        wanted = [d for d in days if d in group.index]
        if not wanted:
            return empty_chain()
        return group.loc[wanted].reset_index(drop=True)

    # ---- filling in ----
    def _prepare(self, frame: pd.DataFrame) -> pd.DataFrame:
        from backtest.data.options.pricing import greeks as bs_greeks, implied_vol

        out = frame.copy()
        for col in CHAIN_COLUMNS:
            if col not in out.columns:
                out[col] = np.nan
        # The document's first data rule: nothing enters without an availability date.
        # When the source supplies none, it is assumed equal to the quote date and the
        # assumption is **recorded**, because a run on data with no availability
        # information is a run whose look-ahead protection rests on an assumption rather
        # than on the data.
        if "available_at" not in frame.columns or out["available_at"].isna().all():
            out["available_at"] = out["asof"]
            self.availability_assumed = True
        else:
            parsed = pd.to_datetime(out["available_at"], errors="coerce").dt.date
            out["available_at"] = parsed.where(parsed.notna(), out["asof"])
            self.availability_assumed = bool(frame["available_at"].isna().any())
        out["source"] = out["source"].astype("object").fillna(f"file:{self.path.name}")
        out["quality_flags"] = out["quality_flags"].astype("object").fillna("")
        # When the column was absent it was created as a float64 NaN column, and
        # writing True into that is a dtype conflict. Fix the type up front
        # rather than relying on pandas' implicit conversion, which newer
        # versions raise on outright.
        out["greeks_model_derived"] = out["greeks_model_derived"].astype("object")

        mid = (out["bid"].astype(float) + out["ask"].astype(float)) / 2.0
        # **The document asks for one time convention everywhere**, so the year's length
        # is imported rather than written here: a 365.0 spelled out in one module and a
        # 252 in another is the inconsistency the rule is about, and it would show up as
        # an IV that disagrees with itself depending on which module solved it.
        tau = np.array([max((e - a).days, 0) / CALENDAR_DAYS_PER_YEAR
                        for a, e in zip(out["asof"], out["expiry"])])
        spot = out["underlying_price"].astype(float).to_numpy()
        strike = out["strike"].astype(float).to_numpy()
        is_call = (out["right"] == Right.CALL.value).to_numpy()
        # The rate is the constant fallback from the config. Once a real curve is
        # wired in this should read per day — as a constant it barely matters for
        # short-dated options, but it biases theta at long tenors.
        rate = self.config.fallback_risk_free_rate

        # The dividend yield each row's IV is solved against, from the market's own
        # ex-dates rather than assumed zero.
        #
        # **It was hardcoded to 0.0**, which biases every in-the-money call: a forward
        # below spot by the dividend makes the true IV higher than a zero-dividend solve
        # returns, and the error grows with the tenor and with how deep the strike is.
        # On an index ETF paying ~0.6% a quarter it is small; on a single name with a 4%
        # yield it is not, and the rows it is worst on are exactly the ones early
        # exercise turns on.
        #
        # Per row rather than one number: the yield depends on how many ex-dates fall
        # before each expiry.
        if hasattr(self.market, "dividend_yield"):
            yields = np.array([
                self.market.dividend_yield(a, e, float(sp))
                for a, e, sp in zip(out["asof"], out["expiry"], spot)
            ])
        else:
            yields = np.zeros(len(out))

        need_iv = out["iv"].isna()
        if need_iv.any():
            # This one stays per row: `implied_vol` is a numerical solve per contract and
            # there is no array form of it. It is the reason to supply an `iv` column when
            # the vendor has one -- solving 200,000 of them is about a minute, and the
            # solve is also where a bad quote turns into an absent IV rather than a wrong
            # one, so a supplied column is both faster and better.
            mid_values = mid.to_numpy(dtype=float)
            # **Which model solves the IV is a choice, and it is the caller's.**
            #
            # The document asks for "a European or American model matching the contract
            # style (US equity and ETF options are American)", and a European solve
            # returns an IV that is too **low** wherever the early-exercise premium is
            # real -- one-sided, and largest on exactly the contracts a researcher looks
            # at hardest.
            #
            # It is not the default, and the reason is measured rather than argued: one
            # American solve is a bisection over a 200-step tree, **13 milliseconds**.
            # On 200,000 rows that is 43 minutes, and screening does not help -- the put
            # case applies whenever rates are positive, which is every put. So the honest
            # arrangement is a European default with the bias stated, and
            # ``iv_model="american"`` for someone who wants the accuracy and will pay for
            # it. The register lists exactly this as a group-1 trade-off: "American vs
            # European IV model -- European is faster; American is accurate for equity
            # and ETF options. Compare IV and Greeks on a sample."
            #
            # The flag goes on every row it applies to, so a result never has to be
            # asked which model produced it.
            use_american = (self.iv_model == "american"
                            and self.config.contract.exercise.value == "american")
            solved = []
            for i in np.flatnonzero(need_iv.to_numpy()):
                right = Right.CALL if is_call[i] else Right.PUT
                if use_american:
                    from backtest.data.options.american import implied_vol_american

                    got = implied_vol_american(
                        price=mid_values[i], spot=spot[i], strike=strike[i],
                        years=tau[i], rate=rate, dividend_yield=yields[i], right=right)
                else:
                    got = implied_vol(mid_values[i], spot[i], strike[i], tau[i], rate,
                                      yields[i], right)
                solved.append(got.unwrap() if got.is_present else np.nan)
            out.loc[need_iv, "iv"] = solved
            out.loc[need_iv & out["iv"].isna(), "quality_flags"] += "|iv_unsolved"
            # Which model produced it, on the row. A European IV on an American contract
            # is biased low where early exercise has value, and a reader comparing two
            # runs has to be able to see which they are looking at.
            out.loc[need_iv, "quality_flags"] += (
                "|iv_american" if use_american else "|iv_european")

        greek_cols = ["delta", "gamma", "vega", "theta"]
        need_greeks = out[greek_cols].isna().any(axis=1)
        if need_greeks.any():
            # **Two vectorised calls, not one per row.**
            #
            # `bs_greeks` takes arrays and the first version called it once per row
            # through `.iloc[i]`, which on 208,000 rows was 42 of the 98 seconds this
            # method took -- and a six-month backtest off a file timed out at ten
            # minutes, which is what found it. Split by right rather than passed a
            # per-row flag, because that is the only input the formula branches on.
            iv = out["iv"].astype(float).to_numpy()
            usable = need_greeks.to_numpy() & np.isfinite(iv) & (tau > 0)
            positions = {col: out.columns.get_loc(col) for col in greek_cols}
            values = out[greek_cols].to_numpy(dtype=float, copy=True)
            for right, side in ((Right.CALL, is_call), (Right.PUT, ~is_call)):
                rows = np.flatnonzero(usable & side)
                if not len(rows):
                    continue
                # The same yield the IV was solved against. Deriving greeks at a
                # different dividend from the one in the IV would make the two describe
                # different options -- and the PDE identity in
                # `tests/test_hand_calculation.py` is what would catch it.
                g = bs_greeks(spot[rows], strike[rows], tau[rows], rate, iv[rows],
                              yields[rows], right)
                for n, col in enumerate(greek_cols):
                    column = np.asarray(g[col], dtype=float)
                    # Only where the vendor did not supply it: a derived greek must never
                    # overwrite one that came with the data.
                    absent = np.isnan(values[rows, n])
                    values[rows[absent], n] = column[absent]
            for n, col in enumerate(greek_cols):
                out.iloc[:, positions[col]] = values[:, n]
            out.loc[need_greeks, "greeks_model_derived"] = True
        out["greeks_model_derived"] = out["greeks_model_derived"].fillna(False).astype(bool)

        out["quality_flags"] = out["quality_flags"].str.strip("|")
        frames = [validate_chain(g, asof=d) for d, g in out.groupby("asof", sort=False)]
        return pd.concat(frames, ignore_index=True) if frames else empty_chain()
