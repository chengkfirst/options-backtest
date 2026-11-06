"""The Details column, held to the files that own it.

## Why the needles are scoped and the earlier ones were not

Three audits in a row over-claimed, each one level finer than the last, and each failed
the same way: **a loose test of a claim about the system**.

- the conformance table missed the component tables, so `Clean` was on no list
- the component table is name level, so `Positions` read built while "sellable quantity
  under the settlement rule" was absent
- the first pass at *this* file probed the whole of `backtest/` for bare words and
  reported five false positives out of fifteen: `frozen` matched
  `@dataclass(frozen=True)`, `margin` matched a comment saying there is **no** margin
  model, `split` matched a sentence about attribution, `interest` matched a docstring,
  `append-only` matched `run_log.py` rather than the ledger

So each clause names the files it would have to live in, and the needle is only looked
for there. A word in `research/` does not make the ledger carry it.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.kinds import kind, labels
from tests.plan_details import CLAUSES, FIGURES

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("architecture", "cross")


def _read(owner: tuple[str, ...]) -> str:
    out = []
    for name in owner:
        path = pathlib.Path(name)
        assert path.exists(), f"a clause names {name}, which does not exist"
        out.append(path.read_text(encoding="utf-8"))
    return "\n".join(out)


@kind("guard")
class TestEveryClauseIsCheckedAgainstItsOwnFiles:
    def test_a_built_clause_has_its_needle_where_it_belongs(self):
        """Scoped, which is the whole correction. The same needle found anywhere in the
        tree is what produced five false positives on the first attempt."""
        missing = []
        for clause in CLAUSES:
            if not clause.built:
                continue
            assert clause.needle, (
                f"{clause.component} · {clause.says[:40]} is marked built and names no "
                f"needle"
            )
            if clause.needle not in _read(clause.owner):
                missing.append(f"{clause.component} · {clause.needle!r} not in "
                               f"{', '.join(clause.owner)}")
        assert not missing, (
            "these are marked built and their needle is not in the file that owns "
            "them:\n  " + "\n  ".join(missing)
        )

    def test_an_absent_clause_says_why_and_what_it_costs(self):
        """A reason, not a label. An absence with no consequence attached is a line item
        somebody will close by writing the word rather than the code."""
        thin = [f"{c.component} · {c.says[:40]}"
                for c in CLAUSES if not c.built and len(c.absent) < 60]
        assert not thin, (
            "these are marked absent without saying what the absence costs:\n  "
            + "\n  ".join(thin)
        )

    def test_every_clause_names_a_file_that_exists(self):
        for clause in CLAUSES:
            _read(clause.owner)

    def test_the_name_level_inventory_does_not_contradict_this(self):
        """**The finding this file exists for.**

        Accounting reads 10/10 at the name level and 16/28 here. That is not a
        contradiction -- a component exists and its details may not -- but it is the
        number that has to be said out loud, because "10/10 built" was being quoted as
        though it meant the layer was done.
        """
        from tests.plan_components import COMPONENTS

        names = {c.component for c in CLAUSES}
        for component in COMPONENTS:
            if component.name not in names:
                continue
            clauses = [c for c in CLAUSES if c.component == component.name]
            if component.built:
                assert any(c.built for c in clauses), (
                    f"{component.name} is marked built at the name level and **every** "
                    f"one of its detail clauses is absent, which is a component that "
                    f"exists in name only"
                )
            else:
                # **Absent at the name level means "not finished", not "not started".**
                #
                # The first form of this rule was `not any(c.built)`, and the data layer
                # broke it immediately: `Adjuster` is marked absent and its dual-price
                # split is built -- the half that matters for an underlying with no
                # corporate actions in range. The same is true of `Universe`, where the
                # option filter exists and the underlying selection has nothing to
                # select among.
                #
                # So the rule the two inventories actually owe each other is the weaker
                # one in each direction: a built component has at least one detail, an
                # absent component is missing at least one. A component marked absent
                # whose every detail is present is the contradiction worth catching,
                # because then the absence is the thing that is wrong.
                assert not all(c.built for c in clauses), (
                    f"{component.name} is marked absent at the name level and **every** "
                    f"one of its detail clauses is built, so the name-level inventory "
                    f"is understating what exists"
                )


class TestTheFiguresWereRead:
    """**The check that would have caught three figures going unread for the whole
    project.**

    The plan is a PDF; the working copy used to build every inventory here is a text
    extraction, and a text extraction drops images. Nothing noticed, because nothing was
    comparing the inventory against the document in a form that contained them.

    So the figure count is read from the PDF itself. A figure added to the document fails
    this, which is the only way "we read the whole plan" stays true as the plan changes.
    """

    PLAN = pathlib.Path("docs/originals/backtest-system-architecture.pdf")

    @kind("guard")
    def test_every_figure_in_the_pdf_is_in_the_inventory(self):
        pytest.importorskip("pypdf")
        # **A failure, not a skip.** This used to skip when the PDF was not where it
        # expected, and the event that would trigger that is precisely the one it must
        # not hide: the file moving. It did move -- into docs/originals/ -- and a skip
        # would have printed OK forever while the only check that reads the whole plan
        # was off.
        assert self.PLAN.exists(), (
            f"{self.PLAN} is not in this checkout. The architecture PDF is the source "
            f"this guard reads; if it moved, this path moves with it rather than the "
            f"guard going quiet")
        import pypdf

        reader = pypdf.PdfReader(str(self.PLAN))
        pages = sorted({i + 1 for i, page in enumerate(reader.pages) if page.images})
        recorded = sorted({page for page, _ in FIGURES})
        assert pages == recorded, (
            f"the document has figures on pages {pages} and the inventory records "
            f"{recorded}. A figure that is in the document and not here has never been "
            f"read against the code -- the text extraction does not contain it"
        )

    def test_each_figure_says_what_it_shows(self):
        """A page number alone records that a figure exists, not that anybody read it."""
        for page, says in FIGURES:
            assert len(says) > 40, f"the figure on page {page} is recorded as {says!r}"


class TestWhatTheDetailsSay:
    def test_the_count_is_printed(self, capsys):
        by_component: dict[str, list[int]] = {}
        for clause in CLAUSES:
            slot = by_component.setdefault(clause.component, [0, 0])
            slot[0 if clause.built else 1] += 1

        built = sum(1 for c in CLAUSES if c.built)
        with capsys.disabled():
            print(f"\n  {len(CLAUSES)} detail clauses transcribed, {built} built, "
                  f"{len(CLAUSES) - built} absent")
            for name, (yes, no) in by_component.items():
                mark = "" if no == 0 else f"   <- {no} absent"
                print(f"    {name:20} {yes}/{yes + no}{mark}")
        assert built, "nothing is built, so the inventory is not being read"
