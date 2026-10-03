"""Every test says what a failure means and where it is — enforced, not encouraged.

Two labels, from ``tests/kinds.py``. A test with neither is a test whose failure has to be
read before it can be triaged, and the point of labelling at all is that it should not.

## Read from the source, not from the session

The same reason as ``test_coverage_map.py``: ``session.items`` holds only what was
*selected*, so running a subset reported kinds as unused that were in plain sight in
another file. **An answer that changes with which subset is running is not an answer.**

## Overriding, not accumulating

A module declares its labels with ``pytestmark``; a class or a test overrides with
``@kind(...)``. pytest marks **accumulate** rather than replace, so a test carrying an
override has two -- and the closest one is the one that applies. Getting this wrong is how
a labelling scheme comes to insist that every override is an error.

## Why this file also measures

The last check reports the files holding several subjects. That is not a style complaint:
the rule the whole scheme rests on is that **each behaviour has one owner**, and a module
covering five subjects cannot be the owner of any of them. Reported rather than failed --
splitting a file is a judgement about what belongs together, and a number is the input to
that judgement rather than a substitute for it.
"""

from __future__ import annotations

import ast
import pathlib
from collections import Counter

from tests.kinds import KINDS, LAYERS, labels

#: This file checks the labelling, so it carries its own.
pytestmark = labels("architecture", "cross")

#: How many distinct subjects a file may hold before it is reported. Three is where a
#: module stops being "this thing and its edges" and starts being a drawer.
SUBJECT_LIMIT = 3

TESTS = pathlib.Path("tests")


def _call_args(node: ast.AST, names: tuple[str, ...]) -> list[str]:
    """String arguments of a call to any of ``names``, wherever it appears."""
    out: list[str] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        fn = ast.unparse(sub.func)
        if fn in names or fn.endswith(tuple(f".{n}" for n in names)):
            out += [a.value for a in sub.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    return out


def resolve() -> dict[str, tuple[str, str]]:
    """``file::test`` -> (kind, layer), with the nearest declaration winning."""
    found: dict[str, tuple[str, str]] = {}
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        module_kind = module_layer = ""
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "pytestmark" for t in node.targets):
                args = _call_args(node, ("labels",))
                if len(args) == 2:
                    module_kind, module_layer = args
                module_kind = (_call_args(node, ("kind",)) or [module_kind])[0]
                module_layer = (_call_args(node, ("layer",)) or [module_layer])[0]

        def walk(body, inherited: tuple[str, str]) -> None:
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    own_kind = _call_args(ast.Module(body=list(node.decorator_list),
                                                    type_ignores=[]), ("kind",))
                    own_layer = _call_args(ast.Module(body=list(node.decorator_list),
                                                      type_ignores=[]), ("layer",))
                    here = (own_kind[0] if own_kind else inherited[0],
                            own_layer[0] if own_layer else inherited[1])
                    if isinstance(node, ast.ClassDef):
                        walk(node.body, here)
                    elif node.name.startswith("test_"):
                        found[f"{path.name}::{node.name}"] = here

        walk(tree.body, (module_kind, module_layer))
    return found


class TestEveryTestIsLabelled:
    def test_each_resolves_to_a_kind_and_a_layer(self):
        resolved = resolve()
        assert resolved, "no tests were found at all, which means this check is broken"
        missing = {name: pair for name, pair in resolved.items()
                   if not pair[0] or not pair[1]}
        assert not missing, (
            f"{len(missing)} tests resolve to no kind or no layer:\n" +
            "\n".join(f"  {k}: {v}" for k, v in list(missing.items())[:20])
        )

    def test_every_label_used_is_a_real_one(self):
        """A typo in a mark is accepted silently by pytest and would create a category
        nobody can query."""
        bad_kinds = {k for k, _ in resolve().values()} - set(KINDS)
        bad_layers = {l for _, l in resolve().values()} - set(LAYERS)
        assert not bad_kinds, f"not kinds: {sorted(bad_kinds)}"
        assert not bad_layers, f"not layers: {sorted(bad_layers)}"


class TestTheShapeOfTheSuite:
    """What the labels are for, beyond triage."""

    def test_every_kind_is_used_by_something(self):
        """A kind nobody uses is a category that was invented rather than observed."""
        used = Counter(k for k, _ in resolve().values())
        # `baseline` is the regression check, which is a tool rather than a pytest test:
        # it lives in tools/baseline.py and is its own guard.
        unused = [k for k in KINDS if k not in used and k != "baseline"]
        assert not unused, (
            f"these kinds are defined and nothing carries them: {unused}. Either "
            f"something should, or the kind is not real"
        )

    def test_there_are_guard_tests_at_all(self):
        """`guard` is the kind whose absence is hardest to notice: a missing unit test
        shows up as an untested function, and a missing guard shows up as nothing."""
        assert Counter(k for k, _ in resolve().values())["guard"] > 0

    def test_there_is_something_computed_outside_this_codebase(self):
        """Every other kind compares the system to itself, and all of them pass on a
        system that has been consistently wrong since it was written."""
        assert Counter(k for k, _ in resolve().values())["known_answer"] > 0

    def test_files_holding_many_subjects_are_reported(self, capsys):
        """The evidence for splitting, rather than taste.

        A module is one subject when its tests share a kind and a layer. Counting the
        distinct pairs inside each file says which modules are drawers -- and a drawer
        cannot be the single owner of any behaviour in it, which is the rule the whole
        scheme rests on.
        """
        by_file: dict[str, set[tuple[str, str]]] = {}
        for name, pair in resolve().items():
            by_file.setdefault(name.split("::")[0], set()).add(pair)

        crowded = {f: sorted(pairs) for f, pairs in by_file.items()
                   if len(pairs) > SUBJECT_LIMIT}
        if crowded:
            with capsys.disabled():
                print(f"\n  files holding more than {SUBJECT_LIMIT} kind/layer "
                      f"combinations — candidates for splitting:")
                for name, pairs in sorted(crowded.items(),
                                          key=lambda x: -len(x[1])):
                    shown = ", ".join(f"{k}/{l}" for k, l in pairs)
                    print(f"    {name:30} {len(pairs)}  ({shown})")
        assert True
