"""Every number this system assumes on your behalf, in the document's four groups.

## Why this file exists

Thresholds leak. A tool needs to turn a measurement into a sentence, so somebody picks a
number — 0.75 of the neighbourhood holding means "a plateau" — and from then on a
**judgement looks like a measurement**. The reader sees "a plateau" and never learns that a
different cut would have said "a slope". And the numbers end up scattered across the
modules that use them, so the only question that matters has no answer: *what has been
decided for me?*

So every one is here, nothing reads a literal, and every verdict names the threshold behind
it.

## The four groups are the document's, not this file's

``Plan/backtest-system-architecture.pdf``, "Research and Trade-off Register", divides every
choice in the system into four groups **and gives each a different rule**. That taxonomy is
the one used here. An earlier version of this file invented its own, which was the same
mistake in miniature: a classification nobody agreed to, presented as structure.

``realism``
    Group 1. Fill price, latency, marking, cleaning rules, the pricing model, the
    attribution method. **Sensitivity tests only, never optimised** — picking the value
    that maximises backtest returns optimises an illusion. Test a few and require the
    conclusion to survive all of them.

``engineering``
    Group 2. Data granularity, check frequency, which engine, the universe, the
    shadow-tracking window. **Chosen once**, for cost and realism. Not a research result.

``strategy``
    Group 3. The snapshot time, contract delta and tenor, the structure, the take-profit
    and stop approaches, headwind sensitivity, hedging, rolling, positioning, the regime
    model, the lifecycle settings, book limits. **May be optimised, but only under the
    protocol** in research-questions section 9. Nothing in this file defaults one of
    these: a tool that needs one asks.

``method``
    Group 4. The walk-forward window, the hold-out length, pooling, the number of variants
    tried, the optimisation objective, the minimum trade count and the bootstrap block
    length. **Chosen before the research, not after seeing results** — which is the whole
    reason they are visible here rather than inline.

The document also names the five that move results most, and they are flagged
:attr:`Threshold.priority`: lambda, the decision latency and the marking method from group
1, and the contract choice and the take-profit approach from group 3.

## How to change one

``override(name=value)`` for a session, or pass the value to the function.
``python -m tools.status`` prints the table, so a drift from the defaults is visible rather
than buried in a call site.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class Threshold:
    """One assumed number."""

    name: str
    value: Any
    #: The document's group: ``realism`` / ``engineering`` / ``strategy`` / ``method``.
    kind: str
    #: What it decides, in one line.
    decides: str
    #: Why this value and not another. Where the answer is "it was chosen", it says so.
    because: str
    #: Where it is used.
    used_by: str
    #: One of the document's five highest-impact choices.
    priority: bool = False

    def __post_init__(self) -> None:
        assert self.kind in GROUPS, f"{self.kind} is not one of the document's groups"

    @property
    def rule(self) -> str:
        """What the document permits doing with a number in this group."""
        return GROUPS[self.kind]


#: The document's rule for each group. Copied here because the rule is what makes the
#: group useful — a classification without a rule is a label.
GROUPS: dict[str, str] = {
    "realism": "sensitivity tests only, never optimised",
    "engineering": "chosen once, for cost and realism",
    "strategy": "may be optimised, but only under the research protocol",
    "method": "chosen before the research, not after seeing results",
}


#: Every threshold, by name. Read through :func:`get`, never as a literal.
THRESHOLDS: dict[str, Threshold] = {
    # ── group 4 · method: chosen before the research, not after seeing results ──
    #
    # Every one of these turns a number into a word. The document puts the choice of a
    # minimum trade count and a bootstrap block length in this group, and these are the
    # same kind of thing: a line drawn before looking, so that looking cannot move it.
    "plateau_share": Threshold(
        "plateau_share", 0.75, "method",
        "what share of a peak's neighbours must hold for the region to be called a plateau",
        "**chosen, not derived.** Three in four neighbours holding is a defensible line "
        "and nothing more; two in three would also be defensible. The share itself is the "
        "measurement, and the word is this cut applied to it",
        "research.sweep.Plateau.verdict",
    ),
    "slope_share": Threshold(
        "slope_share", 0.40, "method",
        "below the plateau share, what share still counts as a usable slope rather than a spike",
        "**chosen, not derived.** Between these two lines the shape is ambiguous, which is "
        "why both words hedge",
        "research.sweep.Plateau.verdict",
    ),
    "plateau_tolerance": Threshold(
        "plateau_tolerance", 0.20, "method",
        "how close to the peak a neighbour must be to count as holding",
        "**chosen.** Relative to the peak's magnitude so it means the same in hundreds or "
        "millions. A tighter cut finds fewer plateaus and a looser one finds plateaus "
        "everywhere; 20% is a middle that nothing derives",
        "research.sweep.plateau",
    ),
    "additive_share": Threshold(
        "additive_share", 0.15, "method",
        "how far a joint effect may sit from the sum of its parts and still be called additive",
        "**chosen.** Below this the two slots can be tuned separately without much error. "
        "The gap and the sum are both reported, so a different line can be drawn",
        "research.ablation.Interaction.verdict",
    ),
    "censored_share_warning": Threshold(
        "censored_share_warning", 0.30, "method",
        "what share of censored trades makes a counterfactual rest on the minority",
        "**chosen.** Any censoring weakens the result; this is where the sentence starts "
        "saying so out loud. The share is always reported regardless",
        "research.counterfactual.Replay.verdict",
    ),
    "single_trade_share": Threshold(
        "single_trade_share", 0.35, "method",
        "what share of total P&L in one trade makes the result one piece of luck",
        "**chosen.** The anomaly scan's job is to raise an eyebrow, not to rule",
        "research.anomaly",
    ),
    "one_year_share": Threshold(
        "one_year_share", 0.70, "method",
        "what share of total P&L in one year makes the strategy environment-dependent",
        "**chosen.** Seven tenths from one year out of several is a strong enough "
        "concentration to say the strategy may only hold in one environment, and weak "
        "enough that it fires before a year is carrying literally everything. The rule "
        "also refuses to fire when total P&L is near zero, because a share of almost "
        "nothing is not a share",
        "research.anomaly",
    ),
    "win_rate_decay": Threshold(
        "win_rate_decay", 0.20, "method",
        "how far the two halves' win rates may differ before the edge is called decaying",
        "**chosen.** Twenty percentage points is a lot on a few hundred trades and not "
        "much on a few thousand, which the rule does not currently account for",
        "research.anomaly",
    ),
    "cost_share_warning": Threshold(
        "cost_share_warning", 0.30, "method",
        "what share of gross P&L in costs means the edge will not survive live execution",
        "**chosen.** Live costs run above backtest costs, so a third is already close to "
        "the line",
        "research.anomaly",
    ),

    # ── group 1 · realism: sensitivity tests only, never optimised ──
    #
    # Both of these mark where a model stops describing what it claims to. Tuning either
    # for a better-looking result would be optimising the illusion the document warns
    # about: a wider large_shock does not make the expansion valid, it just hides that it
    # is not.
    "bootstrap_min_observations": Threshold(
        "bootstrap_min_observations", 10, "method",
        "the fewest observations a bootstrap interval may be built from",
        "a number from under ten observations should be reported as the observations, not "
        "as a statistic with a range. This is not a strictness setting",
        "research.validation.intervals.bootstrap_trades",
    ),
    "block_bootstrap_min_blocks": Threshold(
        "block_bootstrap_min_blocks", 3, "method",
        "the fewest whole blocks a block bootstrap needs",
        "with fewer than three blocks every draw is nearly the same data, so the interval "
        "is an artefact of the resampling",
        "research.validation.intervals.block_bootstrap",
    ),
    "random_entry_min_draws": Threshold(
        "random_entry_min_draws", 10, "method",
        "the fewest random draws a percentile may be computed from",
        "one draw reads as a win or a loss that a single sample cannot support. The "
        "comparison is a position in a distribution",
        "research.baselines.random_entry",
    ),
    "per_period_sharpe_ceiling": Threshold(
        "per_period_sharpe_ceiling", 0.5, "method",
        "the per-period Sharpe above which the figure is assumed to be annualised by mistake",
        "a per-period 0.5 on sub-annual data is an annualised 8, which has never existed. "
        "Pairing an annualised Sharpe with a count of days overstates the deflated result "
        "badly, so the ambiguous case is refused rather than guessed",
        "research.validation.deflated.deflated_sharpe",
    ),
    "large_shock": Threshold(
        "large_shock", 0.10, "realism",
        "the underlying move beyond which a second-order expansion gives no number at all",
        "past this the gamma term overwhelms the delta term and the stress grid returns "
        "results whose **direction** is wrong. A confidently wrong number is worse than "
        "none, so the cells are left empty",
        "portfolio.scenarios.stress_grid",
    ),
    "cash_interest_share": Threshold(
        "cash_interest_share", 1.0, "realism",
        "the share of the risk-free rate the account's idle cash actually earns",
        "paying the full Treasury rate on every dollar of cash is an **assumption about "
        "the broker, not a fact about the market**, and it runs the wrong way: a retail "
        "sweep usually pays below Treasury, and cash pledged against a position pays "
        "nothing. On a long-options book most of the capital sits in cash, so this term "
        "is large next to the trading result -- the fixture earns thousands of interest "
        "against a strategy P&L of hundreds. 1.0 is the optimistic end of the range and "
        "is the default because it is the one figure that is defensible without knowing "
        "the broker; it is sensitivity-tested, never tuned, because a lower share makes "
        "the account look worse and the strategy no better",
        "accounting.ledger.accrue_interest",
    ),
    "attribution_residual_warning": Threshold(
        "attribution_residual_warning", 0.35, "realism",
        "the residual share above which an attribution is not trustworthy",
        "past this the Taylor expansion has failed, so the split into delta, gamma, vega "
        "and theta is not describing the P&L it claims to",
        "accounting.attribution",
    ),

    # ── group 2 · engineering: chosen once, for cost and realism ──
    #
    # The document lists the shadow-tracking window in this group by name. Neither of
    # these is a research result; both trade compute for resolution.
    "random_entry_draws": Threshold(
        "random_entry_draws", 50, "engineering",
        "how many random-entry draws by default",
        "**chosen for patience, not for statistics.** Fifty gives a percentile to within "
        "about two points, and each draw is a full backtest. More draws is strictly "
        "better and strictly slower",
        "research.baselines",
    ),
    "shadow_days": Threshold(
        "shadow_days", 20, "engineering",
        "how many trading days to keep quoting a contract after its trade closed",
        "**chosen as a month of sessions**, which covers the alternative exits anyone "
        "usually asks about. Too short and wider stops come back censored; too long and "
        "every closed trade costs a daily query",
        "engine.loop.EventDrivenEngine",
    ),
}

#: Session overrides, applied on top of the defaults.
_OVERRIDES: dict[str, Any] = {}


def get(name: str) -> Any:
    """The current value. Raises on an unknown name rather than returning a default.

    A typo returning a silent default is how a tool ends up reading a threshold nobody
    set.
    """
    if name not in THRESHOLDS:
        raise KeyError(
            f"no threshold named {name!r}. Known: {sorted(THRESHOLDS)}. Add it to "
            f"core/thresholds.py rather than writing a literal"
        )
    return _OVERRIDES.get(name, THRESHOLDS[name].value)


def override(**values: Any) -> None:
    """Change thresholds for this session. ``tools.status`` shows what is not default."""
    for name, value in values.items():
        if name not in THRESHOLDS:
            raise KeyError(f"no threshold named {name!r}. Known: {sorted(THRESHOLDS)}")
        _OVERRIDES[name] = value


def reset() -> None:
    _OVERRIDES.clear()


def overridden() -> Mapping[str, Any]:
    return dict(_OVERRIDES)


def describe():
    """Every assumed number, what it decides, and whether it is yours to set."""
    import pandas as pd

    rows = []
    for t in THRESHOLDS.values():
        current = get(t.name)
        rows.append({
            "threshold": t.name,
            "group": t.kind,
            "rule": t.rule,
            "default": t.value,
            "in use": current,
            "changed": current != t.value,
            "priority": t.priority,
            "decides": t.decides,
            "used by": t.used_by,
        })
    return pd.DataFrame(rows).set_index("threshold")


def by_group(kind: str) -> list[Threshold]:
    """Every threshold in one of the document's groups.

    The group decides what may be done with the number, so this is the query that matters:
    ``by_group("realism")`` is the list that may be sensitivity-tested and never optimised,
    and ``by_group("method")`` is the list that has to be settled before looking at results.
    """
    if kind not in GROUPS:
        raise KeyError(f"{kind!r} is not one of the document's groups: {sorted(GROUPS)}")
    return [t for t in THRESHOLDS.values() if t.kind == kind]


def strategy_parameters_are_never_defaulted() -> bool:
    """Group 3 holds nothing here, and that is the invariant.

    A strategy choice — which take-profit levels to explore, which delta to target — is the
    researcher's. The document permits optimising them; it does not permit the platform
    picking them. So a tool that needs one takes it as a required argument, and this
    function is what a test asserts against so the rule cannot erode.
    """
    return not any(t.kind == "strategy" for t in THRESHOLDS.values())
