"""A locked hold-out: the one period a result may be scored on once, and not before.

The architecture document's research protocol opens with it -- "lock a final hold-out
period at the start and use it once, at the end" -- and nothing in this system enforced it.
A run could be scored on data already looked at, repeatedly, and neither the result nor the
record of it would say so.

## Why it needs a mechanism rather than discipline

Every other defence here exists because a rule that rests on remembering is a rule that
holds until the week it matters. This one is worse than most: looking at the hold-out does
not fail, does not warn, and produces a number that is **better** than the honest one. The
incentive points the wrong way and the feedback is silent, which is the exact combination
nothing survives by intention.

## What it does

``HoldOut(start, end)`` is a period the researcher declares before the work begins. A run
whose range overlaps it raises, naming the overlap -- unless it is passed ``unlock=`` with
a reason, which is recorded in the run's provenance and in the store. The point is not to
make it impossible; it is to make it **impossible to do silently**.

``ReservedSymbol("SPY")`` is the same promise in the other dimension: an underlying held
out of the whole research. A run on it raises, and records the same way, unless it is
passed ``unlock_symbols=`` with a reason of its own.

Decision 0001 reserved SPY and recorded that ``HoldOut`` could not express it, so "SPY is
reserved" was held by a document and nothing else. **What exists here is the mechanism, not
the enforcement.** Nothing in this repository declares SPY reserved to every caller, and
``run(symbol="SPY")`` without ``hold_out_symbols=`` still proceeds: the reservation holds
only where a caller passes it. The interface cannot declare or read one at all --
``docs/specs/the-interface-reads-the-symbol-hold-out.md`` is that half.

## Why a reserved symbol is its own type

A period and an underlying are not the same kind of thing, and a ``symbol`` field on
``HoldOut`` would make the class answer to two names at once: ``overlaps`` would cover half
of what it reserved, and ``describe`` would report a period while refusing runs for a
reason that has nothing to do with dates. Worse, a symbol reservation has no dates, so
putting it on ``HoldOut`` means inventing a period for it -- and an invented period is
absence dressed as a value, which is the one thing this codebase refuses everywhere else.

Two types also mean the period half is **unchanged** rather than carefully preserved.
``HoldOut`` is the same class it was, and the period logic below is the same code, which is
what the module's docstring, the SDK's wiring and ``tools/check_mutations.py`` all rest on.

## Reserved when *either* matches, never when both do

A run must clear **every** declared reservation: a reserved period refuses on its dates
whatever the symbol, and a reserved symbol refuses on the symbol whatever the dates. The
other reading -- refuse only where a reserved symbol and a reserved period meet -- is a
trap, and it fails in the direction that flatters: declaring SPY and reserving everything
after a date would allow every SPY run before that date, which leaks the reserved
underlying silently while the configuration reads as if two things were held back.

The conjunction is not merely discouraged, it is **unexpressible**: no object here carries
both a symbol and a period, so there is no field combination whose meaning a reader could
guess wrong. Each declaration reserves one thing, and reserving two things reserves both.

**Spending them is separate too, and for the same reason.** ``unlock=`` answers the
period's refusal and ``unlock_symbols=`` answers the symbol's. One ``unlock=`` used to
spend both halves while this module claimed here that it could not: under decision 0001's
own configuration -- SPY reserved, and everything after a date reserved -- a reason written
about dates was recorded as the reason a reserved underlying had been looked at, and no
refusal naming the underlying ever raised.

## One spelling per symbol

``"spy"``, ``"SPY "`` and ``"SPY"`` are one reservation, normalised at construction and
compared the same way. A hold-out that misses because a symbol was lowercase would be
precisely the silent failure this module exists to prevent, and it would miss in the
direction that produces a result rather than an error. The reservation does not rely on
another layer being strict about case, because the layer that is strict today is not the
one that will be asked tomorrow.

The normalising is :func:`_canonical`, in one place, because it was written in three and
the drift between copies fails in the worst direction: change one and a reservation stops
matching, which returns a number instead of an error.

## What it deliberately does not do

It does not pick the period, and it does not pick the symbol. How much to hold back, from
when, and which underlying are research-method choices -- group 4 in the document's
register, decided before the research and not after seeing what helped -- so neither type
invents one: ``HoldOut`` takes two dates, ``ReservedSymbol`` takes a ticker, and there is
no default for either.

**That is not the same as a reservation being required**, and the difference is worth
stating exactly rather than leaving a reader to assume the stronger claim. ``hold_out=``
and ``hold_out_symbols=`` both default to absent on ``run()``; omitting them reserves
nothing, and the record says so. What is enforced is narrower: a declaration that cannot
reserve anything is refused rather than ignored -- an empty symbol, an empty collection of
them, a reservation with no run symbol to compare against -- and a run that reaches a
declared reservation is refused unless the act naming that half unlocks it.

It also does not stop the second use. A hold-out used twice is no longer a hold-out, and a
tool that silently refused a second one would push the work into another process where it
could not be seen. What the record carries is which half was spent and why, which is the
thing a reader needs when deciding how much the final number is worth. Nothing counts the
touches on its own: :func:`touches` computes them from stored records, and no caller
outside the tests invokes it yet.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from backtest.core.errors import StartupError


def _canonical(symbol: str) -> str:
    """One spelling per symbol, defined in exactly one place.

    ``strip().upper()`` was written three times -- at construction, in ``matches``, and
    again for a note's wording -- and two definitions always drift. This one drifts in the
    worst direction: change one copy and a reservation stops matching, which returns a
    result rather than an error.

    A non-string refuses here. ``ReservedSymbol(None)`` raised ``AttributeError`` from
    inside ``strip``, which names neither the parameter nor what was wanted, while every
    other refusal in this module explains itself.
    """
    if not isinstance(symbol, str):
        raise StartupError(
            f"a reserved symbol is the underlying's ticker as text, and this is "
            f"{type(symbol).__name__} ({symbol!r}). Name the underlying, for example "
            f"ReservedSymbol(\"SPY\")."
        )
    return symbol.strip().upper()


@dataclass(frozen=True, slots=True)
class HoldOut:
    """A period reserved from the research, declared before it starts."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise StartupError(
                f"a hold-out ends before it begins: {self.start} to {self.end}"
            )

    def overlaps(self, start: date, end: date) -> "tuple[date, date] | None":
        """The overlapping stretch, or ``None``."""
        lo, hi = max(self.start, start), min(self.end, end)
        return (lo, hi) if lo <= hi else None

    def describe(self) -> str:
        return f"{self.start} to {self.end}"


@dataclass(frozen=True, slots=True)
class ReservedSymbol:
    """An underlying reserved from the research, declared before it starts.

    It carries no dates, which is the point: what is reserved is the underlying itself,
    for the whole research, so no shorter range makes a run on it allowed. See the module
    docstring for why this is a sibling of ``HoldOut`` rather than a field on it.
    """

    symbol: str

    def __post_init__(self) -> None:
        # Normalised once, here, so every later comparison is against the canonical form
        # and no caller has to remember to upper-case. An empty reservation is refused
        # rather than ignored: it reads in a configuration as something held back, and
        # silently reserves nothing.
        canonical = _canonical(self.symbol)
        if not canonical:
            raise StartupError(
                "a reserved symbol with no symbol in it reserves nothing, while reading "
                "in the configuration as a reservation. Name the underlying, for example "
                "ReservedSymbol(\"SPY\")."
            )
        object.__setattr__(self, "symbol", canonical)

    def matches(self, symbol: str) -> bool:
        """Whether a run on ``symbol`` is a run on this reserved underlying."""
        return _canonical(symbol) == self.symbol

    def describe(self) -> str:
        return self.symbol


def check(hold_out: "HoldOut | None", *, start: date, end: date,
          unlock: str = "", symbol: str = "", unlock_symbols: str = "",
          reserved: "Sequence[ReservedSymbol] | None" = None) -> dict:
    """Refuse a run that reaches into the hold-out, unless it is unlocked with a reason.

    Returns what belongs in the run's provenance: whether a hold-out was declared, whether
    this run touched it, and the reason given. A run that did **not** touch it says so too
    -- "no hold-out was declared" and "declared and respected" are different states, and a
    reader comparing two results needs to tell them apart.

    Both halves report in one mapping and each keeps its own three states: ``hold_out`` /
    ``touched`` / ``reason`` / ``note`` for the period, ``symbols`` / ``symbol_touched`` /
    ``symbol_spent`` / ``symbol_reason`` / ``symbol_note`` for the underlying. They are
    **not** merged into one ``touched`` flag or one ``reason``: a run that spent the
    reserved symbol while respecting the reserved period has not been scored out of sample
    in time, and a single flag would let it claim it was.

    **Each half is unlocked only by the act that names it** -- ``unlock=`` for the period,
    ``unlock_symbols=`` for the underlying. ``reserved=None`` means nothing was declared by
    symbol; an empty collection is a declaration that reserves nothing and is refused.
    """
    # The symbol half goes first. Its refusal is the one no change to the run's dates can
    # fix, so reporting it first does not send a reader off to shorten a range that was
    # never the problem.
    by_symbol = _symbols(reserved, symbol=symbol, unlock_symbols=unlock_symbols)
    by_period = _period(hold_out, start=start, end=end, unlock=unlock,
                        also_reserved=_symbol_only_note(by_symbol, symbol=symbol))
    return {**by_period, **by_symbol}


def _symbol_only_note(by_symbol: dict, *, symbol: str) -> str:
    """What the period half says when a symbol is reserved and no period is -- or ``""``.

    **The subject has to be explicit.** This note read "...so a result on another
    underlying is out of sample in the underlying and in sample in time", and it is
    attached to a run that *is* on another underlying. Its natural subject is the result it
    is attached to, so it told a reader their QQQ result was out of sample in the
    underlying, which is false -- and false in the flattering direction. What stays out of
    sample is the reserved underlying; this result is in sample in both dimensions.
    """
    names = by_symbol.get("symbols")
    if not names:
        return ""
    head = ("no hold-out period was declared, and what is reserved here is reserved by "
            "symbol")
    spent = by_symbol.get("symbol_spent")
    if spent:
        return (f"{head}. This run was scored on {spent}, which was reserved, so this "
                f"result is in sample in both the underlying and the period")
    verb = "stays" if len(names) == 1 else "stay"
    return (f"{head}: {', '.join(names)} {verb} out of sample, while this result, on "
            f"{_canonical(symbol)}, is in sample in both the underlying and the period")


def _period(hold_out: "HoldOut | None", *, start: date, end: date, unlock: str,
            also_reserved: str) -> dict:
    """The period half -- the same logic it has always had.

    ``also_reserved`` is the note to use when nothing was reserved by period while
    something was reserved by symbol; without it this half would report that nothing was
    reserved from the research while an underlying was. Everything else here, and the
    refusal in particular, is what it was before the symbol half existed.
    """
    if hold_out is None:
        if also_reserved:
            return {"hold_out": None, "touched": False, "note": also_reserved}
        return {"hold_out": None, "touched": False,
                "note": "no hold-out was declared, so nothing was reserved from this "
                        "research and no result here is out of sample"}

    overlap = hold_out.overlaps(start, end)
    if overlap is None:
        return {"hold_out": hold_out.describe(), "touched": False,
                "note": f"the run ends before the hold-out ({hold_out.describe()}) "
                        f"begins"}

    if not unlock:
        raise StartupError(
            f"this run covers {overlap[0]} to {overlap[1]}, which is inside the hold-out "
            f"{hold_out.describe()}.\n\n"
            f"The hold-out exists to be used once, at the end. Scoring on it earlier does "
            f"not fail and does not warn -- it just returns a better number than the "
            f"honest one, which is why this refuses rather than cautions.\n\n"
            f"If this is the final evaluation, pass unlock=\"<why>\". The reason is "
            f"recorded with the run, and the tracker counts how many times the hold-out "
            f"has been touched."
        )

    return {"hold_out": hold_out.describe(), "touched": True, "reason": unlock,
            "note": f"this run was scored on {overlap[0]} to {overlap[1]}, inside the "
                    f"hold-out. Every later result on this data is in sample"}


def _symbols(reserved: "Sequence[ReservedSymbol] | None", *, symbol: str,
             unlock_symbols: str) -> dict:
    """The symbol half, with the same three states as the period half.

    ``None`` is "nothing was declared". An **empty collection is a declaration that
    reserves nothing**, and is refused: it reserved nothing, refused nothing, and wrote
    provenance identical to omitting the parameter, so a full backtest on the reserved
    underlying completed with its record reading "no symbol was reserved". It is almost
    never written as a literal -- it is a comprehension that filtered everything out, or a
    configuration key read as absent. Reporting ``symbols: []`` instead would not be
    enough, because nothing downstream reads the difference.

    That is the rule ``ReservedSymbol("   ")`` already applied one level down, and the one
    ``engine/runner.py`` applies to an empty batch: "almost always a list comprehension
    that filtered everything out".
    """
    if reserved is None:
        return {"symbols": None, "symbol_touched": False,
                "symbol_note": "no symbol was reserved, so no underlying is held out of "
                               "this research"}

    reservations = tuple(reserved)
    if not reservations:
        raise StartupError(
            "a reservation of no symbols reserves nothing, while reading in the "
            "configuration as an underlying held back. An empty collection here is "
            "almost always a comprehension that filtered everything out, or a "
            "configuration key read as absent -- and the run would then be scored on the "
            "reserved underlying while its record read as a run that reserved nothing.\n\n"
            "Name the underlying, for example hold_out_symbols=(\"SPY\",), or leave the "
            "parameter out, which reserves nothing and says so."
        )

    wrong = next((r for r in reservations if not isinstance(r, ReservedSymbol)), None)
    if wrong is not None:
        # Not coerced. Normalising lives in `ReservedSymbol`, so a bare " spy" accepted
        # here would be compared unnormalised, miss a run on SPY, and return a result
        # rather than an error -- the failure this module exists to prevent.
        raise StartupError(
            f"a reservation must be a ReservedSymbol, and this one is "
            f"{type(wrong).__name__} ({wrong!r}). Wrap it, so that one spelling per "
            f"symbol is settled where the reservation is made: "
            f"reserved=(ReservedSymbol(\"SPY\"),)."
        )

    names = sorted({r.symbol for r in reservations})
    joined = ", ".join(names)
    if not symbol.strip():
        # A reservation that is never told what the run is on matches nothing and warns
        # nobody, which is the failure mode of the whole module. Loud, not lenient.
        raise StartupError(
            f"a symbol is reserved ({joined}) but this check was not told which symbol "
            f"the run is on, so the reservation could not have matched anything. Pass "
            f"symbol=\"<the run's underlying>\"."
        )

    canonical = _canonical(symbol)
    reached = next((r for r in reservations if r.matches(symbol)), None)
    # Written as one clause rather than nested under the match so that the period half's
    # refusal above stays the single anchor `tools/check_mutations.py` names for it. An
    # anchor that appears twice is reported rather than skipped, which is the guard
    # working -- but it would be reported as this module's problem, and it is not one.
    if reached is not None and not unlock_symbols:
        raise StartupError(
            f"this run is on {reached.describe()}, which is reserved ({joined}).\n\n"
            f"A reserved underlying is held out of the whole research rather than out of "
            f"a stretch of it, so no shorter range makes this run allowed. It exists so "
            f"that one underlying stays independent of every choice made while looking at "
            f"the others, and that independence is spent the first time it is looked at "
            f"-- silently, and in the direction that returns a better number than the "
            f"honest one.\n\n"
            f"If this is the final evaluation, pass unlock_symbols=\"<why>\". It is "
            f"separate from unlock=, which answers a reserved period: a reason written "
            f"about dates is not a decision to spend an underlying. The reason is "
            f"recorded in this run's provenance and in the store, where "
            f"research.holdout.touches() lists every run that spent part of a hold-out "
            f"and why -- nothing counts them for you, and nothing refuses a second use."
        )

    if reached is None:
        verb = "stays" if len(names) == 1 else "stay"
        return {"symbols": names, "symbol_touched": False,
                "symbol_note": f"this run is on {canonical}, which is not reserved; "
                               f"{joined} {verb} reserved"}

    # `symbol_spent` is **which** reservation this run spent, as a fact beside the
    # declaration rather than inside the prose below. `touches` joined `symbols` and so
    # reported every reserved underlying as spent: a SPY run with SPY and IWM reserved
    # read as "IWM, SPY" gone, and a reader who believes an intact hold-out is spent has
    # no reason left to protect it.
    return {"symbols": names, "symbol_touched": True, "symbol_spent": reached.symbol,
            "symbol_reason": unlock_symbols,
            "symbol_note": f"this run was scored on {reached.describe()}, which was "
                           f"reserved. Every later result on this underlying is in "
                           f"sample"}


def touches(records) -> list[dict]:
    """Every stored run that reached into its hold-out, with the reason given.

    The number a person needs when deciding what a final figure is worth: a hold-out
    touched once is the protocol working, and touched five times is a validation period
    that has quietly become a training one.

    A spent underlying counts as a touch beside a spent period, and ``reserved`` says
    which it was -- two runs, one on the reserved symbol and one in the reserved period,
    have each used part of what was held back, and a count that saw only one of them
    would understate how much of the hold-out is gone.

    **What it reports is what was spent, not what was declared.** It joined the whole
    ``symbols`` list, so a run on SPY with SPY and IWM reserved reported both as spent
    while IWM had never been run. The declaration stays in ``symbols``; the matched
    reservation is ``symbol_spent``, and that is what is joined here.
    """
    out = []
    for record in records:
        provenance = record.get("hold_out") or {}
        by_period = bool(provenance.get("touched"))
        by_symbol = bool(provenance.get("symbol_touched"))
        if by_period or by_symbol:
            spent = [what for what in (
                provenance.get("hold_out") if by_period else None,
                provenance.get("symbol_spent") if by_symbol else None,
            ) if what]
            # Each half's own reason, in order and deduplicated. Carrying one of them
            # would name the wrong reason for half of what was spent, which is the
            # record defect that made a single `unlock=` look adequate.
            reasons = dict.fromkeys(what for what in (
                provenance.get("reason") if by_period else None,
                provenance.get("symbol_reason") if by_symbol else None,
            ) if what)
            out.append({
                "run_id": record.get("run_id", ""),
                "when": record.get("when", ""),
                "reason": "; ".join(reasons),
                "reserved": "; ".join(spent),
            })
    return out
