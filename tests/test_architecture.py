"""Architectural constraints: dependency direction, pricing staying confined. A
document cannot stop an import; this can."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import tools.check_deps as deps

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("architecture", "cross")


def test_dependency_direction_holds():
    assert deps.check() == []


def test_detects_reverse_dependency(tmp_path, monkeypatch):
    """With a reverse import planted deliberately, the check must fail — otherwise
    this defence is fake."""
    package = tmp_path / "backtest"
    (package / "data").mkdir(parents=True)
    (package / "data" / "leak.py").write_text(
        "from backtest.engine import trade_table\n", encoding="utf-8"
    )
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    problems = deps.check()
    assert any("reverse dependency" in p for p in problems)


def test_detects_pricing_leak(tmp_path, monkeypatch):
    package = tmp_path / "backtest"
    (package / "engine").mkdir(parents=True)
    (package / "engine" / "leak.py").write_text(
        "from backtest.data.options.pricing import bs_price\n", encoding="utf-8"
    )
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    problems = deps.check()
    assert any("pricing is confined to the data layer" in p for p in problems)


#: Where a float that might be absent is a **measurement**, so absence has to be
#: `Maybe[float]`. Elsewhere -- strategy rules, selection rules, plans -- `float | None`
#: means "no such rule" and is correct.
DATA_MODULES = ("backtest/core/types", "backtest/data", "backtest/accounting")


def test_no_bare_optional_floats_across_module_boundaries():
    """Scalars crossing module boundaries express absence with Maybe, never a bare
    `float | None`.

    A missing delta read as 0 selects a completely wrong contract and raises nothing.
    """
    def is_bare_optional_float(node) -> bool:
        """`float | None`, `None | float` and `Optional[float]`, which are the same type.

        The first version matched only the `|` spelling, so writing `Optional[float]`
        slipped past a check whose whole point is that the type is forbidden however it is
        written.
        """
        if node is None:
            return False
        text = ast.unparse(node).replace(" ", "")
        return text in {"float|None", "None|float", "Optional[float]",
                        "typing.Optional[float]"}

    offenders: list[str] = []
    for path in sorted(Path("backtest").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            # A function's return type.
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                if is_bare_optional_float(node.returns):
                    offenders.append(f"{path}:{node.lineno} {node.name} -> "
                                     f"{ast.unparse(node.returns)}")
            # A dataclass field, which crosses module boundaries just as a return does
            # and was not looked at. Only in the modules that carry **observed** data: a
            # `delta: float | None` on a market record is read by every consumer and a
            # missing one read as 0 picks the wrong contract.
            #
            # Configuration is excluded, and that is the rule rather than an exemption. On
            # a strategy rule `stop_loss: float | None` means **there is no stop**, which
            # is a real configuration the code branches on explicitly -- the opposite of a
            # measurement that went missing. Forcing Maybe there would make "no rule" and
            # "unknown" the same shape, which is the confusion this whole convention
            # exists to prevent, in the other direction.
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id.startswith("_") or not any(
                        str(path).startswith(d) for d in DATA_MODULES):
                    continue
                if is_bare_optional_float(node.annotation):
                    offenders.append(f"{path}:{node.lineno} {node.target.id}: "
                                     f"{ast.unparse(node.annotation)}")
    assert offenders == [], "use Maybe[float] instead:\n" + "\n".join(offenders)


@pytest.mark.req("0.4.regression")
@kind("baseline")
def test_regression_baseline_unchanged():
    """The baseline comparison: an unintended change in results must be caught, never
    pass silently."""
    import tools.baseline as baseline

    diff = baseline.compare()
    assert diff is None, (
        "the output differs from the baseline:\n"
        + diff.to_string(index=False)
        + "\nIf the change really should move the results, run "
          "`python -m tools.baseline update` explicitly"
    )


#: The three methods that change the book. Everything else on the ledger reads it.
LEDGER_WRITES = ("apply_fill", "settle_expiry", "liquidate_stock",
                 # Added when the Details column's cash clauses landed. The guard below
                 # caught them the same run: a method that moves cash and is not here
                 # could be called from a deciding component and the scan would stay
                 # green, which is the one thing this list is for.
                 "accrue_interest", "credit_dividend", "deposit",
                 "freeze", "unfreeze")

#: Where decisions are made: the slots the document says may read positions and must not
#: place orders. The engine is deliberately absent -- placing orders is its whole job.
DECIDING_MODULES = ("backtest/signals", "backtest/portfolio")

#: Slots whose implementation sits **inside** the engine, so the scan below cannot cover
#: them. Found by the check rather than known in advance, and recorded rather than worked
#: around: ``time_exit`` -- the holding cap and the days-to-expiry floor -- is evaluated
#: in the day loop, which is the one place a write to the ledger is allowed.
#:
#: What that costs is specific. Every other slot is structurally unable to fill its own
#: order; this one is only not doing so. It is also the slot whose attribution was wrong
#: until recently (its exits were credited to the stop), and the two facts have the same
#: cause: it is not a component, so nothing that holds components to a shape holds it.
#: Moving it out is a design change rather than a correction, so it is named here for
#: whoever decides, and the exemption is checked below rather than asserted.
SLOTS_INSIDE_THE_ENGINE = ("time_exit",)


@pytest.mark.req("2.3.boundary")
def test_no_deciding_component_writes_to_the_ledger():
    """Exit, headwind and hedge read positions and return decisions; the engine fills.

    The separation is what makes a component swappable: a slot that could fill its own
    order would also decide the order of events, and two slots doing that would produce a
    result that depends on which ran first. It is also what makes the two engines
    comparable at all -- a component that wrote to the book would write to a different
    book in each.

    This requirement had a mark on it, on the test that forbids bare ``float | None``
    across module boundaries, which is a different rule entirely. Nothing checked this
    one. Written structurally rather than by running a component and inspecting the
    ledger afterwards: an attempt that was never made leaves no trace to assert on.
    """
    def writes_in(prefixes: tuple[str, ...]) -> list[str]:
        found: list[str] = []
        for path in sorted(Path("backtest").rglob("*.py")):
            if not any(str(path).startswith(d) for d in prefixes):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Attribute) \
                        and node.func.attr in LEDGER_WRITES:
                    found.append(f"{path}:{node.lineno} calls .{node.func.attr}()")
        return found

    # The positive control, run first. An empty result below means "no component writes
    # to the book" only if this scan can see a write when there is one -- and the engine
    # is full of them, because filling orders is what it is for. Without this, a detector
    # that matched nothing at all would read as a clean bill of health.
    assert writes_in(("backtest/engine",)), (
        "the scan found no ledger writes in the engine, where they certainly are, so it "
        "is not detecting them anywhere and the check below is vacuous"
    )

    offenders = writes_in(DECIDING_MODULES)
    assert offenders == [], (
        "these are deciding components and they change the book directly:\n"
        + "\n".join(offenders)
        + "\n\nA slot returns a decision and the engine fills it. A slot that fills its "
          "own order decides the order of events too, and the result then depends on "
          "which slot ran first."
    )


def test_the_deciding_modules_are_the_real_ones():
    """The other half of the check's own list.

    Pointing ``DECIDING_MODULES`` at a directory that does not exist makes the scan find
    no offenders and the check pass -- the emptiest possible green. The positive control
    inside the check covers the detector; this covers the list it is pointed at.

    Held against the slot registry rather than a second hand-written list: every slot a
    component can fill has an implementation somewhere, and whichever directory holds it
    is a directory where a write to the ledger would be the failure the check is for.
    """
    from backtest.core.component import SLOTS

    for prefix in DECIDING_MODULES:
        directory = Path(prefix)
        assert directory.is_dir(), (
            f"{prefix} is not a directory, so the scan over it finds nothing and the "
            f"check passes by looking at no files"
        )
        assert list(directory.rglob("*.py")), f"{prefix} holds no Python files"

    # Every slot's name should appear somewhere under these prefixes. A slot whose
    # implementation lives outside them is a slot the check is not looking at.
    covered = "\n".join(
        p.read_text(encoding="utf-8")
        for prefix in DECIDING_MODULES for p in Path(prefix).rglob("*.py"))
    missing = [slot for slot in SLOTS
               if slot not in covered and slot not in SLOTS_INSIDE_THE_ENGINE]
    assert not missing, (
        f"these slots are not mentioned anywhere under {DECIDING_MODULES}: {missing}. "
        f"Either their implementations live somewhere the scan does not reach, or the "
        f"list of deciding modules is out of date"
    )

    # And the exemption has to be real: a slot listed here as living in the engine and
    # actually living in a component directory would be exempted for nothing.
    engine = "\n".join(p.read_text(encoding="utf-8")
                       for p in Path("backtest/engine").rglob("*.py"))
    for slot in SLOTS_INSIDE_THE_ENGINE:
        assert slot in engine, f"{slot} is exempted as living in the engine and does not"


def test_the_ledger_write_list_is_the_real_one():
    """The check above is only as good as its list of what counts as a write.

    A method added to the ledger that changes the book and is not named here would pass
    the check silently -- which is the shape of every defence in this project that turned
    out to be decorative. So the list is held against the ledger itself: a public method
    that mutates state has to be in it or this fails.
    """
    import backtest.accounting.ledger as mod

    source = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    ledger = next(n for n in source.body
                  if isinstance(n, ast.ClassDef) and n.name == "Ledger")

    mutating = []
    for node in ledger.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        if any(isinstance(d, ast.Name) and d.id == "property"
               for d in node.decorator_list):
            continue
        # A method that assigns to an attribute of `self`, or calls a known mutator on
        # one of its collections, changes the book.
        writes = any(
            (isinstance(sub, (ast.Assign, ast.AugAssign))
             and "self." in ast.unparse(sub.targets if isinstance(sub, ast.Assign)
                                        else [sub.target]))
            or (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in ("append", "pop", "clear", "update")
                and ast.unparse(sub.func.value).startswith("self."))
            for sub in ast.walk(node)
        )
        if writes:
            mutating.append(node.name)

    missing = sorted(set(mutating) - set(LEDGER_WRITES))
    assert not missing, (
        f"these ledger methods change the book and are not in LEDGER_WRITES: {missing}. "
        f"Until they are, a component could call one and the check above would stay "
        f"green"
    )
    unused = sorted(set(LEDGER_WRITES) - set(mutating))
    assert not unused, (
        f"these are listed as ledger writes and no longer exist or no longer write: "
        f"{unused}"
    )


def test_no_optional_dependency_is_defaulted_with_or():
    """``x = arg or fallback`` on anything with a length, where None was meant.

    ``EventLog`` defines ``__len__``, so an **empty** log is falsy and
    ``self.events = events or market.events`` silently discarded a log a caller had passed
    deliberately. Nothing raised, nothing warned; the events went to the shared log, and a
    test asserting "nothing was recorded here" read another test's events instead.

    The same shape is waiting in every `or` used as a default for a container, a frame or a
    number: an empty list, an empty DataFrame and 0.0 are all falsy and all legitimate
    values. So the check is structural -- ``or`` is not how an optional argument gets its
    default in this codebase; ``if x is not None`` is.

    Scoped to assignments whose target is an attribute or the parameter itself, which is
    the defaulting idiom. ``a or b`` inside an expression is left alone.
    """
    offenders: list[str] = []
    for path in sorted(Path("backtest").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            optional = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                value = node.value
                if not isinstance(value, ast.BoolOp) or not isinstance(value.op, ast.Or):
                    continue
                first = value.values[0]
                if not isinstance(first, ast.Name) or first.id not in optional:
                    continue
                target = ast.unparse(node.targets[0])
                if not (target.startswith("self.") or target == first.id):
                    continue
                offenders.append(f"{path}:{node.lineno} {target} = "
                                 f"{ast.unparse(value)}")

    assert offenders == [], (
        "these default an optional argument with `or`, which also replaces an empty "
        "list, an empty frame, a zero or an empty log that was passed on purpose:\n"
        + "\n".join(offenders)
        + "\n\nUse `x if x is not None else fallback`."
    )


def test_the_day_loop_disposes_of_delivered_shares():
    """The call site, not the method.

    ``_dispose_deliveries`` has behavioural tests that drive it directly, and they pass
    with the **call** in the day loop deleted -- so the method is covered and the wiring
    is not. The gap is the same one every "it works when I call it myself" test leaves,
    and it matters here because an in-the-money expiry does not occur on the offline
    fixture, so no run can exercise the path end to end.

    Asserted structurally for that reason: the day loop must call it, and must do so
    before settling expiries, or shares delivered on a day would be sold on the same day
    -- before delivery.
    """
    source = Path("backtest/engine/loop.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    engine = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.ClassDef) and n.name == "EventDrivenEngine")
    run = next(n for n in engine.body
               if isinstance(n, ast.FunctionDef) and n.name == "run")

    calls = [n for n in ast.walk(run)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    names = [n.func.attr for n in calls]
    assert "_dispose_deliveries" in names, (
        "the day loop does not dispose of delivered shares, so a physical settlement "
        "leaves the underlying in the book for the rest of the run"
    )
    assert "settle_expiry" in names or any(
        n.func.attr == "_settle" for n in calls), "the loop no longer settles expiries"

    dispose_line = min(n.lineno for n in calls if n.func.attr == "_dispose_deliveries")
    settle_line = min(n.lineno for n in calls if n.func.attr == "_settle")
    assert dispose_line < settle_line, (
        "the disposal runs after settlement in the same day, so shares delivered today "
        "would be sold today -- before delivery, which is overnight"
    )
