"""Cleaning rules: what to do about bad data, as a choice that can be switched off.

## What the document asks for

Layer 1's pipeline is ingest -> validate -> **clean** -> adjust -> point-in-time -> IV and
greeks -> surface -> universe -> features -> store. The clean stage's own line is:

    Apply explicit rules. Every rule configurable and logged; suspended or quote-less
    contracts are marked untradable, never forward-filled.

## Why `check_frame` is not this

``adapters.check_frame`` **reports**. It finds the wide spreads, the locked markets, the
asks below intrinsic, and it tells you. Nothing acts on it, so between "this data has
2,000 wide-spread rows" and "the run used them" there was nothing at all: a dataset was
refused whole or used whole.

That is not merely a missing convenience. The Research and Trade-off Register puts
**data-cleaning rules in group 1** -- sensitivity tests only, never optimised -- with the
test written out: "compare results with and without, focusing on stress periods". The
reason is in the same row: cleaning gives cleaner data and *may remove stressed periods
and bias results toward calm markets*. Running that comparison requires cleaning to be a
thing that can be turned off, which requires it to be a thing at all.

## Two ways to treat a row, and the document chooses between them

- **drop** it: the row leaves the dataset
- **mark untradable**: the row stays, with its prices, and no order may be filled against
  it

The document is explicit that a quote-less or suspended contract is marked untradable and
**never forward-filled**. Marking is the weaker action and the better default: a dropped
row is invisible to everything downstream, including the count of how much was dropped,
while a marked row still carries its own evidence. A rule here says which it does, and
the report says how many of each.

## What is deliberately not here

**No rule is on by default.** A platform-chosen cleaning rule is the platform deciding
which market conditions a strategy is tested through, and the register's whole point about
this group is that the choice biases the result in a direction nobody notices. So
``Cleaning()`` with no rules changes nothing, and that is the state every existing run is
in.

No fixing of prices either. A rule may drop a row or mark it untradable; nothing here
writes a price, an IV or a greek. A cleaned price is a price somebody made up, and the
one thing worse than bad data is bad data that looks fine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from backtest.core.errors import StartupError

#: What a rule may do to the rows it matches.
ACTIONS: tuple[str, ...] = ("drop", "mark_untradable")

#: The flag written into ``quality_flags`` for a marked row, so the execution layer and
#: the trade record both see it. Reused rather than invented per rule: a reader grepping
#: for why a fill was refused should find one word.
UNTRADABLE_FLAG = "untradable"


@dataclass(frozen=True, slots=True)
class Rule:
    """One cleaning rule: what it matches, what it does, and why.

    ``reason`` is required. A dataset that lost 2% of its rows to a rule nobody can name
    is a dataset whose provenance has a hole in it, and the register's sensitivity test --
    results with and without, in stress periods -- cannot be read without knowing which
    rule did what.
    """

    name: str
    #: ``f(frame) -> boolean Series``: which rows this rule is about.
    matches: Callable[[pd.DataFrame], Any]
    action: str
    reason: str

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise StartupError(
                f"{self.name!r} has action {self.action!r}; one of {', '.join(ACTIONS)}"
            )
        if not self.reason:
            raise StartupError(
                f"the cleaning rule {self.name!r} has no reason. A dataset that lost "
                f"rows to a rule nobody can name has a hole in its provenance, and the "
                f"register's with-and-without comparison cannot be read without it"
            )
        if not self.name:
            raise StartupError("a cleaning rule needs a name; it is what the report "
                               "attributes rows to")


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """What one rule did."""

    name: str
    action: str
    reason: str
    matched: int
    #: Rows this rule was the **first** to act on. A row several rules match is attributed
    #: to the first, so the figures sum to the total rather than double-counting.
    acted: int

    def describe(self) -> str:
        verb = "dropped" if self.action == "drop" else "marked untradable"
        extra = ("" if self.matched == self.acted
                 else f" ({self.matched - self.acted} already handled by an earlier rule)")
        return f"{self.name}: {self.acted} rows {verb}{extra} -- {self.reason}"


@dataclass(frozen=True, slots=True)
class CleaningReport:
    """What cleaning did to a dataset, rule by rule.

    Carried into the run's provenance rather than printed and forgotten: a result from
    cleaned data and one from raw data are different results, and the difference has to
    travel with them.
    """

    rows_in: int
    rows_out: int
    outcomes: tuple[RuleOutcome, ...]

    @property
    def dropped(self) -> int:
        return self.rows_in - self.rows_out

    @property
    def marked(self) -> int:
        return sum(o.acted for o in self.outcomes if o.action == "mark_untradable")

    @property
    def touched_share(self) -> float:
        if not self.rows_in:
            return 0.0
        return (self.dropped + self.marked) / self.rows_in

    def text(self) -> str:
        if not self.outcomes:
            return ("no cleaning rules were given, so the data is as the source "
                    "supplied it")
        lines = [f"Cleaning · {self.rows_in:,} rows in, {self.rows_out:,} out",
                 "─" * 68]
        for outcome in self.outcomes:
            lines.append(f"  {outcome.describe()}")
        lines += [
            "",
            f"  {self.dropped:,} dropped, {self.marked:,} marked untradable "
            f"({self.touched_share:.2%} of rows touched).",
            "",
            "  **Cleaning is a group-1 choice: test its sensitivity, never optimise it.**",
            "  Cleaner data can remove stressed periods and bias a result toward calm",
            "  markets, so the reading that counts is the same run with these rules off,",
            "  compared over the stressed stretches rather than over the whole range.",
        ]
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows_in": self.rows_in, "rows_out": self.rows_out,
            "dropped": self.dropped, "marked": self.marked,
            "rules": [{"name": o.name, "action": o.action, "reason": o.reason,
                       "matched": o.matched, "acted": o.acted}
                      for o in self.outcomes],
        }


@dataclass(frozen=True, slots=True)
class Cleaning:
    """An ordered set of cleaning rules.

    Ordered because the order decides attribution: a row matching two rules is acted on
    by the first, and the report says so rather than counting it twice. Nothing else
    depends on the order -- a drop and a mark on the same row would otherwise differ, so
    a drop wins wherever both apply and the mark is recorded as already handled.
    """

    rules: tuple[Rule, ...] = ()

    def version(self) -> tuple[tuple[str, str, str], ...]:
        """The rule set as values, for the run fingerprint.

        **The document's second data rule is that cleaning is replayable: the same raw
        data and the same rule version give the same result.** Replayable was already
        true -- the rules are pure functions of the frame and nothing here writes a
        price -- and *checkable* was not: the rule set never reached the run id, so a run
        that dropped every crossed quote and a run that kept them had the same
        fingerprint and a search over the two looked like one run repeated.

        Name, action and reason rather than the matcher, because a function object has no
        stable identity across processes. That makes this a version of the rule set's
        **declaration**: editing a matcher's body without touching its name would not
        move the id -- which is what the commit hash in the fingerprint is for, and the
        two together cover it.
        """
        return tuple((r.name, r.action, r.reason) for r in self.rules)

    def apply(self, frame: pd.DataFrame) -> tuple[pd.DataFrame, CleaningReport]:
        """Clean a frame. Returns the frame and what was done to it.

        **Never forward-fills and never writes a price.** A rule drops a row or marks it
        untradable; a cleaned price is a price somebody made up.
        """
        if not self.rules:
            return frame, CleaningReport(rows_in=len(frame), rows_out=len(frame),
                                          outcomes=())

        out = frame.copy()
        if "quality_flags" not in out.columns:
            out["quality_flags"] = ""
        out["quality_flags"] = out["quality_flags"].fillna("").astype(str)

        handled = np.zeros(len(out), dtype=bool)
        to_drop = np.zeros(len(out), dtype=bool)
        outcomes: list[RuleOutcome] = []

        for rule in self.rules:
            hit = np.asarray(rule.matches(out), dtype=bool)
            if hit.shape != (len(out),):
                raise StartupError(
                    f"the cleaning rule {rule.name!r} returned {hit.shape} for "
                    f"{len(out)} rows. A matcher returns one boolean per row"
                )
            fresh = hit & ~handled
            if rule.action == "drop":
                to_drop |= fresh
            else:
                flags = out["quality_flags"].to_numpy(dtype=object)
                for i in np.flatnonzero(fresh):
                    current = flags[i]
                    flags[i] = (f"{current}|{UNTRADABLE_FLAG}:{rule.name}"
                                if current else f"{UNTRADABLE_FLAG}:{rule.name}")
                out["quality_flags"] = flags
            handled |= fresh
            outcomes.append(RuleOutcome(name=rule.name, action=rule.action,
                                         reason=rule.reason, matched=int(hit.sum()),
                                         acted=int(fresh.sum())))

        kept = out.loc[~to_drop].reset_index(drop=True)
        return kept, CleaningReport(rows_in=len(frame), rows_out=len(kept),
                                     outcomes=tuple(outcomes))


# ── rules the document names, offered and not applied ─────────────────────────
#
# Each is a constructor rather than an instance, because each takes a threshold and the
# threshold is the researcher's: a 20% spread cap and a 50% one keep different markets,
# and a platform-supplied number would be the platform deciding which conditions a
# strategy is tested through.

def wide_spread(*, max_share_of_mid: float) -> Rule:
    """Mark rows whose spread exceeds a share of the mid.

    Marked rather than dropped by default, which is the document's own instruction for an
    untradable quote. A dropped row is invisible downstream, including to the count of
    what was dropped; a marked one still carries its prices, so research can see what was
    excluded from trading and at what cost.

    This is the register's own example of a cleaning rule, and its warning applies
    directly: wide spreads cluster in stressed markets, so a cap removes exactly the
    periods a long-options strategy most needs to survive.
    """
    if not 0 < max_share_of_mid < 10:
        raise StartupError(
            f"max_share_of_mid={max_share_of_mid} is not a share of the mid. 0.20 means "
            f"a spread up to 20% of the mid is tradable"
        )

    def matches(frame: pd.DataFrame):
        mid = (frame["bid"].astype(float) + frame["ask"].astype(float)) / 2.0
        spread = frame["ask"].astype(float) - frame["bid"].astype(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            share = np.where(mid > 0, spread / mid, np.inf)
        return share > max_share_of_mid

    return Rule(name=f"wide_spread>{max_share_of_mid:.0%}", matches=matches,
                action="mark_untradable",
                reason=f"the spread exceeds {max_share_of_mid:.0%} of the mid, so a "
                       f"fill there costs more than the model's half-spread. **Wide "
                       f"spreads cluster in stressed markets**, so this removes the "
                       f"periods the strategy most needs to survive")


def no_quote() -> Rule:
    """Mark rows with no two-sided quote.

    The document: a quote-less contract is marked untradable, **never forward-filled**.
    Forward-filling is what makes a halted contract look tradable at yesterday's price,
    which is the single most flattering thing a backtest can do with missing data.
    """
    def matches(frame: pd.DataFrame):
        bid = frame["bid"].astype(float)
        ask = frame["ask"].astype(float)
        return (~np.isfinite(bid)) | (~np.isfinite(ask)) | (bid <= 0) | (ask <= 0)

    return Rule(name="no_quote", matches=matches, action="mark_untradable",
                reason="no two-sided quote, so there is no price to trade at. Never "
                       "forward-filled: a halted contract at yesterday's price is the "
                       "most flattering thing a backtest can do with missing data")


def crossed_quote() -> Rule:
    """Drop rows where the bid exceeds the ask.

    Dropped rather than marked, and this is the one case where dropping is right: a
    crossed quote is not a wide market or a halt, it is **arithmetically impossible**. The
    row is not a worse price, it is not a price. Keeping it would leave a negative spread
    in every statistic computed over the dataset.
    """
    def matches(frame: pd.DataFrame):
        return frame["bid"].astype(float) > frame["ask"].astype(float)

    return Rule(name="crossed_quote", matches=matches, action="drop",
                reason="the bid is above the ask, which is not a worse price but not a "
                       "price at all. Dropped rather than marked: kept, it leaves a "
                       "negative spread in every statistic over the dataset")


def below_intrinsic(*, tolerance: float = 0.005) -> Rule:
    """Drop rows offered below intrinsic value.

    Against the **ask**: a price you could have paid below intrinsic is free money, so the
    row is bad data rather than a cheap option. A bid below intrinsic is ordinary and is
    not matched -- nobody is obliged to bid fair value.
    """
    def matches(frame: pd.DataFrame):
        spot = frame["underlying_price"].astype(float)
        strike = frame["strike"].astype(float)
        is_call = frame["right"].astype(str).str.upper().str.startswith("C")
        intrinsic = np.where(is_call, np.maximum(spot - strike, 0.0),
                             np.maximum(strike - spot, 0.0))
        return frame["ask"].astype(float) < intrinsic - tolerance

    return Rule(name="below_intrinsic", matches=matches, action="drop",
                reason="offered below intrinsic value, which is arbitrage and therefore "
                       "bad data: a backtest buying there books a profit nobody could "
                       "have taken")
