"""The plan's component inventory, held to the code — both directions.

## The failure this exists for

I reported "65 of 82 requirements built" for weeks. The 82 come from the document's
**conformance table**, which is one row per section and is a summary. The document also
names its components directly, in a table per layer — **seventy-five of them** — and that
inventory was never transcribed.

So Layer 1 had six requirements in the map, and the document's own submodule table names
fourteen. `Clean`, `Adjust`, `SurfaceBuilder` and `Universe` were on no list at all, and
the figure I kept quoting was answering a narrower question than the one being asked.

## Why a needle and not a judgement

Presence has to be decided by something that cannot drift. A reviewer reading the list
and nodding is what produced the gap in the first place.

And the needle has to be **specific**. The first version of this audit probed `Clean` with
the word `untradable`, which appears in the order-rejection path and has nothing to do
with a cleaning stage — four of eighteen findings were false positives from loose needles,
in an audit written *because* the previous audit was wrong. So the tests below check both
directions: a built component's needle must be findable, and an absent one's must not be.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.kinds import kind, labels
from tests.plan_components import COMPONENTS

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("architecture", "cross")

#: Where a component may live. ``app/`` is included because Layer 7 is there.
ROOTS = ("backtest", "app")


def _sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for root in ROOTS:
        for path in pathlib.Path(root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            out[str(path)] = path.read_text(encoding="utf-8")
    return out


@pytest.fixture(scope="module")
def sources():
    return _sources()


def _where(sources: dict[str, str], needle: str) -> str:
    return next((p for p, text in sources.items() if needle in text), "")


@kind("guard")
class TestTheInventoryMatchesTheCode:
    def test_every_component_is_either_built_or_says_why_not(self):
        """An absence with a reason is an answer; one without is a question nobody has
        looked at — which is exactly the state `Clean` was in."""
        silent = [c.name for c in COMPONENTS if not c.built and len(c.absent) < 30]
        assert not silent, (
            f"these are marked absent without saying why: {silent}"
        )

    def test_a_built_component_has_a_needle_and_it_is_findable(self, sources):
        """The claim, held to the source. A component marked built whose needle is gone
        has either moved or been deleted, and both are things to know."""
        missing = []
        for component in COMPONENTS:
            if not component.built:
                continue
            assert component.needle, f"{component.name} is built and names no needle"
            if not _where(sources, component.needle):
                missing.append(f"{component.name} ({component.needle!r})")
        assert not missing, (
            "these are marked built and their needles are nowhere in the source:\n  "
            + "\n  ".join(missing)
        )

    def test_an_absent_component_is_really_absent(self, sources):
        """The other direction, and the one that catches a stale reason.

        `Adjust`, `SurfaceBuilder` and `Report` were reported present by a first pass
        whose needles were loose — `"adjusted"`, `"def surface"`, `"reports/"` — each of
        which matches something unrelated. A component carrying an absence reason while
        its own named thing exists is the mirror of the failure this file is for.
        """
        wrong = []
        for component in COMPONENTS:
            if component.built or not component.needle:
                continue
            found = _where(sources, component.needle)
            if found:
                wrong.append(f"{component.name}: {component.needle!r} in {found}")
        assert not wrong, (
            "these say they are absent and their needle is in the source:\n  "
            + "\n  ".join(wrong)
        )

    def test_the_two_inventories_are_both_covered(self):
        """The conformance table and the component table are different questions, and
        this is the file that says so.

        A requirement is a **behaviour** that can be falsified; a component is a **thing**
        that exists or does not. Neither answers the other, and quoting one as though it
        were both is the mistake this file was written after.
        """
        from tests.requirements_map import REQUIREMENTS

        assert len(REQUIREMENTS) >= 80, "the conformance table lost rows"
        assert len(COMPONENTS) >= 70, "the component table lost rows"
        # No name is in both lists under the same spelling, which would mean one had
        # been copied into the other rather than transcribed from the document.
        shared = {c.name for c in COMPONENTS} & {r.id for r in REQUIREMENTS}
        assert not shared, f"these appear in both inventories: {sorted(shared)}"


@kind("guard")
class TestTheTwoInventoriesAgree:
    """Where both tables describe the same thing, they must say the same thing.

    This is the next failure mode after the one that produced this file: two lists of
    the same system, maintained separately, drifting until a requirement is marked built
    and the component it rests on is marked absent. Each would read fine on its own.

    Only the pairs that genuinely describe one thing are listed. A requirement is a
    behaviour and a component is a thing, so most of them do not pair at all -- and
    inventing a pairing to raise the count would make the check fail on a difference
    that is not one.
    """

    #: ``(requirement id, component name)`` where both describe the same piece.
    PAIRS = (
        ("1.quality", "Validator"), ("1.corporate", "Adjuster"),
        ("1.rule1", "PITalignment"), ("0.3.cut", "Access"),
        ("0.1.bus", "EventLoop"), ("0.2.state", "Runner"),
        ("2.1.vocabulary", "SignalCombiner"), ("3.1.actions", "RiskRule"),
        ("3.2.target", "OrderGenerator"), ("3.3.scenario", "ScenarioRule"),
        ("4.2.fees", "CostModel"), ("4.2.exercise", "ExerciseModel"),
        ("4.2.early_exercise", "ExerciseModel"),
        ("5.1.two_engines", "VectorizedEngine"), ("5.1.resume", "Runner"),
        ("5.2.tradebook", "TradeBook"), ("5.3.attribution", "GreeksAttribution"),
        ("6.1.metrics", "Metrics"), ("6.1.report", "Report"),
        ("6.4.trials", "ExperimentTracker"), ("7.1.api", "JobQueue"),
        ("7.2.live", "LiveAdapter"),
    )

    def test_both_names_in_a_pair_exist(self):
        """A pair naming something neither inventory holds is a pair that checks
        nothing, which is how a cross-check comes to pass vacuously."""
        from tests.plan_components import BY_NAME
        from tests.requirements_map import BY_ID

        unknown = [f"{rid}/{cname}" for rid, cname in self.PAIRS
                   if rid not in BY_ID or cname not in BY_NAME]
        assert not unknown, f"these pairs name something not in an inventory: {unknown}"

    def test_a_paired_requirement_and_component_agree(self):
        from tests.plan_components import BY_NAME
        from tests.requirements_map import BY_ID

        disagree = []
        for rid, cname in self.PAIRS:
            requirement, component = BY_ID[rid], BY_NAME[cname]
            if bool(not requirement.not_built) != component.built:
                disagree.append(
                    f"{rid} is "
                    f"{'built' if not requirement.not_built else 'not built'} and "
                    f"{cname} is {'built' if component.built else 'absent'}")
        assert not disagree, (
            "the two inventories describe the same pieces differently:\n  "
            + "\n  ".join(disagree)
        )


class TestWhatTheInventorySays:
    """Read as a report rather than a pass or fail, like the other measurements here."""

    def test_the_count_is_printed(self, capsys):
        built = [c for c in COMPONENTS if c.built]
        absent = [c for c in COMPONENTS if not c.built]
        by_layer: dict[str, list[int]] = {}
        for component in COMPONENTS:
            slot = by_layer.setdefault(component.layer, [0, 0])
            slot[0 if component.built else 1] += 1

        with capsys.disabled():
            print(f"\n  the plan names {len(COMPONENTS)} components: "
                  f"{len(built)} built, {len(absent)} absent")
            for layer, (yes, no) in by_layer.items():
                print(f"    {layer:12} {yes}/{yes + no}")
        assert built, "nothing is built, which means the inventory is not being read"
