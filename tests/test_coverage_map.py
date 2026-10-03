"""Which requirements no test claims — a question with an answer, not a memory exercise.

``tools/check_plan.py`` asks whether each *section* of the document has been read against
the system. This asks the finer question: of the individual requirements inside those
sections, which has nobody claimed.

Three passes over the document by eye each missed different things, which is the evidence
that attention is the wrong tool. A list that goes red is the right one.

## What a claim is, and what it is not

A test claims a requirement with ``@pytest.mark.req("0.4.cash")``. The claim is **not**
evidence that the requirement holds -- nothing automatic can be. It is evidence that
somebody looked. The distinction matters because the failure this prevents is not "the
test was wrong", it is "there was no test and nobody knew".

Two things keep the claim from becoming decoration:

- every requirement must say what would falsify it, so a requirement that cannot be
  tested is visible as such when it is written rather than when someone tries
- a requirement the system does not meet carries ``not_built`` with the reason, so
  "uncovered because it is absent" and "uncovered because nobody looked" are different
  answers rather than the same silence
"""

from __future__ import annotations

import pathlib

import pytest

from tests.requirements_map import BY_ID, IDS, REQUIREMENTS


def _claimed() -> dict[str, list[str]]:
    """Requirement id -> the tests that claim it.

    Read from the test files rather than from the running session. The session holds only
    what was *selected*, so running this file alone reported every requirement as
    uncovered -- an answer that depends on which subset is running is not an answer. The
    marker is a literal string, so the source is a reliable place to read it.
    """
    import ast

    out: dict[str, list[str]] = {}
    for path in sorted(pathlib.Path("tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if ast.unparse(decorator.func) not in ("pytest.mark.req", "mark.req"):
                    continue
                for arg in decorator.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        out.setdefault(arg.value, []).append(f"{path}::{node.name}")
    return out


@pytest.fixture(scope="module")
def claims() -> dict[str, list[str]]:
    return _claimed()


class TestTheMapItself:
    """The list has to hold up before anything is measured against it."""

    def test_every_requirement_says_what_would_falsify_it(self):
        """A requirement that cannot name its own falsification collects a test that
        cannot fail. That is not hypothetical here: a test in this suite was called "a
        broken rule does not kill the scan" and its body was a bare call with the comment
        "it is enough that it does not raise"."""
        silent = [r.id for r in REQUIREMENTS if not r.falsified_by.strip()]
        assert not silent, silent

    def test_ids_are_unique(self):
        assert len(IDS) == len(REQUIREMENTS)

    def test_every_id_names_its_section(self):
        """So a failure says where in the document to look."""
        wrong = [r.id for r in REQUIREMENTS
                 if not r.id.startswith(r.section.replace(".", "."))]
        assert not wrong, wrong

    def test_every_section_of_the_document_has_at_least_one_requirement(self):
        """The coarse check and the fine one have to agree about what exists."""
        from tools.check_plan import SECTIONS

        # Sections with no requirements of their own: the strategy model and the
        # directory structure are structural rather than behavioural, and 6.2 is empty in
        # the document itself.
        structural = {"D", "6.2", "R2"}
        sections = {r.section for r in REQUIREMENTS} | structural
        missing = [num for num, _ in SECTIONS if num not in sections]
        assert not missing, (
            f"these document sections have no requirement transcribed: {missing}"
        )


class TestCoverage:
    def test_no_requirement_is_claimed_by_a_test_that_does_not_exist(self, claims):
        unknown = sorted(set(claims) - IDS)
        assert not unknown, (
            f"these tests claim requirements that are not in the map: {unknown}. Either "
            f"the id is misspelled or the requirement needs transcribing"
        )

    def test_every_built_requirement_is_claimed(self, claims):
        """The question this file exists to answer.

        A requirement the system does not meet is exempt **and says why** -- that is an
        answer. One that is built and claimed by nothing is the failure.
        """
        uncovered = sorted(r.id for r in REQUIREMENTS
                           if not r.not_built and r.id not in claims)
        report = "\n".join(f"  - {rid}  ({BY_ID[rid].section})  {BY_ID[rid].text}"
                           for rid in uncovered)
        assert not uncovered, (
            f"{len(uncovered)} requirements the system claims to meet have no test "
            f"claiming them:\n{report}\n\n"
            f"Tag the test that covers it with @pytest.mark.req(\"<id>\"), or record in "
            f"requirements_map.py why it is not built."
        )

    def test_the_unbuilt_ones_say_why(self):
        """So "not covered" never has to be investigated twice."""
        silent = [r.id for r in REQUIREMENTS if r.not_built and len(r.not_built) < 20]
        assert not silent, f"these say they are not built without saying why: {silent}"

    def test_a_not_built_requirement_is_not_also_claimed(self, claims):
        """If something now covers it, it is built -- and the map should say so rather
        than keep an excuse that has expired."""
        stale = sorted(r.id for r in REQUIREMENTS if r.not_built and r.id in claims)
        assert not stale, (
            f"these are marked not built and have tests claiming them: {stale}. Remove "
            f"the not_built reason, or remove the claim"
        )
