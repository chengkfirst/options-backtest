"""The guard that holds the documents to the code, and its own positive controls.

## Why a guard needs tests more than most code does

``tools/check_doc_claims.py`` reports nothing when the documents are current, which is
the same output it produces when its parser is broken. That is not hypothetical: the first
version matched the status cell with a regex whose greedy group captured whatever followed
the **last** pipe of a markdown row -- the empty string -- so every row read as having no
status and the whole check passed on a document with six stale claims in it.

A guard silent because it cannot see is worse than no guard. It reads as green.

So each check here is exercised twice: once against a document that is wrong, to prove it
bites, and once against one that is right, to prove it does not fire on prose that merely
mentions the words.
"""

from __future__ import annotations

import pytest

import tools.check_doc_claims as claims

from tests.kinds import labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("architecture", "cross")


class TestTheLayoutBrackets:
    """``[absent: a.py b.py]`` in the README, against what is on disk."""

    def test_the_real_documents_agree(self):
        assert claims.check() == []

    def test_a_name_that_exists_is_reported(self, tmp_path, monkeypatch):
        readme = tmp_path / "README.md"
        readme.write_text("```\nbacktest/\n  core/   types.py\n"
                          "                   [absent: clock.py]\n```\n",
                          encoding="utf-8")
        monkeypatch.setattr(claims, "ROOT", tmp_path)
        monkeypatch.setattr(claims, "LAYOUT_ROOT",
                            claims.Path(__file__).resolve().parent.parent / "backtest")
        problems = claims._layout_problems()
        assert any("clock.py" in p for p in problems), (
            "clock.py is listed absent from core/ and exists, and the check said nothing"
        )

    def test_a_name_resolves_inside_its_own_block_only(self, tmp_path, monkeypatch):
        """The false positive that made the first version unusable.

        ``core/``'s absent ``events.py`` matched ``research/events.py``, its
        ``registry.py`` matched ``signals/registry.py``, and ``options/``'s
        ``contracts.py`` matched ``signals/contracts.py`` -- three at once, all wrong. A
        guard with false positives is a guard that gets waived.
        """
        readme = tmp_path / "README.md"
        readme.write_text("```\nbacktest/\n  execution/   costs.py\n"
                          "                   [absent: registry.py]\n```\n",
                          encoding="utf-8")
        monkeypatch.setattr(claims, "ROOT", tmp_path)
        monkeypatch.setattr(claims, "LAYOUT_ROOT",
                            claims.Path(__file__).resolve().parent.parent / "backtest")
        # signals/registry.py and core/component.py's registry both exist;
        # execution/registry.py does not.
        assert claims._layout_problems() == []

    def test_the_documents_own_aside_is_not_read_as_a_name(self, tmp_path, monkeypatch):
        """``[absent: pnl.py — all inside ledger.py]`` names where the missing pieces
        live. Reading ``ledger.py`` as absent would report the document for being right.

        The aside is stripped from the joined block rather than line by line: a bracket
        wraps, so the dash can sit on a different line from the name it introduces --
        which is how ``component.py`` came to look like a name on the second attempt.
        """
        readme = tmp_path / "README.md"
        readme.write_text("```\nbacktest/\n  accounting/   tradebook.py\n"
                          "                   [absent: pnl.py — all inside\n"
                          "                            ledger.py]\n```\n",
                          encoding="utf-8")
        monkeypatch.setattr(claims, "ROOT", tmp_path)
        monkeypatch.setattr(claims, "LAYOUT_ROOT",
                            claims.Path(__file__).resolve().parent.parent / "backtest")
        assert claims._layout_problems() == []


class TestTheStatusRows:
    """A section's prose status against ``requirements_map.py``, which is the same
    judgement as data and is already held to the test suite."""

    @staticmethod
    def _run(monkeypatch, tmp_path, row: str):
        # Written at the path `check_doc_claims` reads, which is `docs/requirements.md`
        # since the layout tidy. A fixture that writes the basename the module used to
        # read would make every case here raise FileNotFoundError -- loudly, which is
        # the good kind, but it is still a fixture agreeing with a stale memory rather
        # than with the code.
        target = tmp_path / "docs" / "requirements.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(row + "\n", encoding="utf-8")
        monkeypatch.setattr(claims, "ROOT", tmp_path)
        return claims._status_problems()

    def test_a_built_section_called_absent_is_reported(self, monkeypatch, tmp_path):
        """3.1's two requirements are both built."""
        problems = self._run(monkeypatch, tmp_path,
                             "| 3.1 | four risk actions | **absent** | — |")
        assert any("3.1" in p for p in problems)

    def test_a_section_with_nothing_built_called_built_is_reported(self, monkeypatch,
                                                                   tmp_path):
        """7.2's one requirement is not built."""
        problems = self._run(monkeypatch, tmp_path,
                             "| 7.2 | the same code live | **built** | — |")
        assert any("7.2" in p for p in problems)

    def test_the_word_in_ordinary_prose_is_not_a_status(self, monkeypatch, tmp_path):
        """Four of the first six findings were this.

        "the gap closed was not a missing action", "absent columns are derived and
        flagged", "(2) and (5) are missing; see the table below" -- every one a sentence
        explaining what is **not** missing. Bold is how the document marks a status, so
        bold is what is read.
        """
        for row in (
            "| 3.1 | four risk actions | Done. The gap was not a missing action | — |",
            "| 3.1 | my own data can be wired in | done | absent columns are derived |",
            "| 3.1 | each component has a way in | partial | (2) and (5) are missing |",
        ):
            assert self._run(monkeypatch, tmp_path, row) == [], row

    def test_a_status_in_the_third_of_four_columns_is_still_read(self, monkeypatch,
                                                                 tmp_path):
        """The document's tables put the status in different columns. Taking the last
        cell is what made the check vacuous the first time -- in a markdown row the text
        after the final pipe is the empty string."""
        problems = self._run(monkeypatch, tmp_path,
                             "| 3.1 | four risk actions | **absent** | a note here |")
        assert problems, "a status in the third column of four was not read"

    def test_a_partial_section_is_not_reported_either_way(self, monkeypatch, tmp_path):
        """0.1 has one requirement and it is partial. Neither word applies, and
        inventing a complaint would train people to waive this."""
        assert self._run(monkeypatch, tmp_path,
                         "| 0.1 | events are the only way | **partial** | — |") == []
