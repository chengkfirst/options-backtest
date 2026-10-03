"""Two labels on every test: what kind of problem a failure is, and which layer it is in.

A failing test should answer two questions before anyone opens it. **Which layer** comes
from the architecture document's own numbering. **What kind** answers something the layer
cannot: whether the code got an answer wrong, or a defence stopped working, or two parts
quietly disagree -- which are different situations calling for different responses.

The one worth separating out is ``GUARD``. A guard failing does not mean the thing it
guards is broken; it means **nothing is watching it any more**, which is worse, because the
next real error passes unseen. A unit failure is a bug. A guard failure is a bug detector
going dark.

## The kinds

``UNIT``          one function's arithmetic. Failure: the code computes the wrong number.
``GUARD``         a defence. Failure: a defence stopped working, which outlasts any one bug.
``CONSISTENCY``   two independent implementations or records agreeing. Failure: semantics
                  have quietly forked -- the two engines, a config file against its run, a
                  fingerprint against what it covers.
``KNOWN_ANSWER``  agreement with a figure computed outside this codebase. Failure: a
                  formula has been wrong since it was written, which every
                  engine-against-engine test passes straight through.
``RESEARCH``      a research tool telling the truth. Failure: a plausible wrong number
                  reaches a conclusion.
``ARCHITECTURE``  a structural constraint. Failure: a dependency direction or a convention
                  has been broken.
``LIMIT``         **a known gap, asserted as still being there.** Not a defence and not
                  arithmetic: a statement that something cannot currently be expressed or
                  detected, shaped a particular way. When the gap closes, the test fails
                  and tells you to delete it -- which is the only way a recorded limitation
                  stops being a comment nobody re-reads.

``BASELINE`` is its own thing and deliberately not one of the above: it asks whether the
result moved, and a failure means *something* changed without saying what kind of thing.

## How a test gets its labels

A module declares its own with ``pytestmark``; a class or a test overrides where it
differs. ``tests/test_labels.py`` requires every test to resolve to exactly one of each.

The override count is also the measurement for splitting files: a module needing many
overrides is a module holding several subjects, and that is evidence rather than taste.
"""

from __future__ import annotations

import pytest

#: What a failure means.
KINDS: tuple[str, ...] = (
    "unit", "guard", "consistency", "known_answer", "research", "architecture",
    "limit", "baseline",
)

#: Where it is. The document's own numbering, plus ``cross`` for the few that span layers
#: by nature -- the dependency direction, the requirement map.
LAYERS: tuple[str, ...] = ("0", "1", "2", "3", "4", "5", "6", "7", "cross")


def kind(name: str):
    """Mark a module, class or test with what a failure means."""
    if name not in KINDS:
        raise ValueError(f"{name!r} is not a kind; one of {', '.join(KINDS)}")
    return pytest.mark.kind(name)


def layer(name: str):
    """Mark a module, class or test with the layer it belongs to."""
    if str(name) not in LAYERS:
        raise ValueError(f"{name!r} is not a layer; one of {', '.join(LAYERS)}")
    return pytest.mark.layer(str(name))


def labels(kind_name: str, layer_name: str) -> list:
    """Both at once, for a module's ``pytestmark``."""
    return [kind(kind_name), layer(layer_name)]
