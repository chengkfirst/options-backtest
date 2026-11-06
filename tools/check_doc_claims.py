"""Do the documents' **claims** still describe this codebase.

Beside ``tools/check_docs.py``, which runs the README's code blocks. That one catches an
example that no longer works; this one catches a sentence that is no longer true, which
nothing could catch before.

## Why this is a guard and not a reading habit

``README.md`` and ``docs/requirements.md`` are the project's own account of itself: what exists,
what does not, and why. Both are written in prose, and prose does not fail. A document
claiming machinery the code does not have is the precise failure this project refuses --
it has happened here, in ``core/component.py``, whose docstring described two methods it
did not have -- and the mirror of it is worse: a document still saying something is
**absent** after it was built sends the next reader to build it again.

Today's commit landed the event bus. ``README.md`` went on listing ``events.py`` as absent
and ``docs/requirements.md`` went on calling it "the largest structural gap", because nothing
anywhere connects the sentence to the file.

## What is checked

Two things, both mechanical, because a check that needs judgement is a check that gets
waived.

1. **The layout's square brackets.** ``README.md`` marks every directory and module the
   architecture document specifies and this codebase does not have, as
   ``[absent: a.py b.py]``. Each name is looked for on disk. One that exists is reported:
   the document is behind the code.

2. **The status words against the requirements map.** ``docs/requirements.md`` gives each
   document section a status -- built, partial, absent, missing. ``tests/requirements_map.py``
   holds the same judgement per requirement, as data, and the test suite already fails when
   a requirement marked built has no test. So the two can be held against each other: a
   section whose every requirement is built must not read **absent**, and one with no built
   requirement at all must not read **built**.

What is deliberately not checked is the prose itself. Whether a paragraph explains the
reasoning well is a judgement, and encoding it here would produce a guard that fails on
rewording.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Where the layout tree's bracketed names are resolved from. A bracket inside the
#: ``backtest/`` tree block names something under that package.
LAYOUT_ROOT = ROOT / "backtest"

#: Names that appear in a bracket and are not files: a trailing slash is a directory, and
#: a parenthetical is the document's own aside.
_NOT_A_NAME = re.compile(r"^[(—-]|^all\b|^inside\b")


#: A bracket names things missing from **the directory whose block it is in**. Resolving
#: a name anywhere under ``backtest/`` instead produced three false positives at once --
#: ``core/``'s absent ``events.py`` matched ``research/events.py``, its ``registry.py``
#: matched ``signals/registry.py``, ``options/``'s ``contracts.py`` matched
#: ``signals/contracts.py``. A guard with false positives is a guard that gets waived, so
#: the block's own directory is tracked down the tree.
_DIR_LINE = re.compile(r"^(\s*)([a-z_]+)/\s")


def _bracketed(text: str) -> list[tuple[int, str, str]]:
    """``(line number, directory, name)`` for every name inside an ``[absent: ...]``.

    The blocks wrap across lines, so a bracket opened on one line is attributed to the
    directory most recently named at or above its indentation -- which is how the tree
    reads to a person.
    """
    out: list[tuple[int, str, str]] = []
    #: indentation -> directory, so a nested block (``options/`` under ``data/``) resolves
    #: to the nested path and a sibling block does not inherit it.
    stack: list[tuple[int, str]] = []
    open_block: tuple[int, str] | None = None
    collected: list[str] = []

    for number, line in enumerate(text.splitlines(), start=1):
        dir_match = _DIR_LINE.match(line)
        if dir_match and "[absent" not in line.split("/")[0]:
            indent, name = len(dir_match.group(1)), dir_match.group(2)
            stack = [(i, d) for i, d in stack if i < indent]
            stack.append((indent, name))

        here = "/".join(d for _, d in stack)
        if "[absent:" in line:
            open_block, collected = (number, here), []
            body = line.split("[absent:", 1)[1]
        elif open_block is not None:
            body = line
        else:
            continue

        closed = "]" in body
        collected.append(body.split("]", 1)[0])
        if not closed:
            continue

        # The document's own aside -- "— all inside ledger.py", "— the variant registry
        # lives in component.py" -- names where the missing pieces live rather than
        # something missing. Everything after the dash is prose, and the dash can be on a
        # different line from the name it introduces: a bracket wraps, so the aside is
        # stripped from the **joined block** and not line by line. Doing it per line left
        # `component.py` looking like a name because its dash was on the line above.
        joined = re.split(r"[\u2014-]{1,2}\s", " ".join(collected))[0]
        for raw in joined.split():
            name = raw.strip(",;")
            if not name or _NOT_A_NAME.match(name):
                continue
            out.append((open_block[0], open_block[1], name))
        open_block, collected = None, []
    return out


def _layout_problems() -> list[str]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    problems: list[str] = []
    for line, directory, name in _bracketed(readme):
        where = LAYOUT_ROOT / directory / name.rstrip("/")
        if where.exists():
            # Shown relative to the package root rather than to ``ROOT``: the two are the
            # same in a real run and are not under a test that points ROOT elsewhere, and
            # a reporting line that raises turns a finding into a crash.
            problems.append(
                f"README.md:{line} lists {name!r} as absent from {directory}/ and it "
                f"exists: {LAYOUT_ROOT.name}/{directory}/{name.rstrip('/')}"
            )
    return problems


#: The status words the requirements document uses, **in bold**, which is how it marks a
#: status rather than mentions a word.
#:
#: Bold is the whole of what makes this check reliable. Matching the words anywhere in the
#: cell produced four false positives out of six on the first run -- "the gap closed was
#: not a *missing* action", "*absent* columns are derived and flagged", "(2) and (5) are
#: *missing*; see the table below" -- every one a sentence explaining what is **not**
#: missing. A guard with false positives is a guard that gets waived, and a waived guard
#: is worse than none because it reads as green.
_BUILT_WORDS = ("built",)
_NOTHING_WORDS = ("absent", "missing", "not started", "not built")

#: A bold span: ``**like this**``.
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _status_problems() -> list[str]:
    """Section status rows in ``docs/requirements.md`` against the requirements map."""
    from tests.requirements_map import REQUIREMENTS

    by_section: dict[str, list] = {}
    for req in REQUIREMENTS:
        by_section.setdefault(req.section, []).append(req)

    text = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8").splitlines()
    problems: list[str] = []
    for number, line in enumerate(text, start=1):
        # A status row: `| 0.1 | what the document asks | the status |`
        #
        # Split on the pipes rather than matched with a regex. The regex version --
        # `^\|\s*(...)\s*\|(.*)\|(.*)$` -- captured as the status whatever followed the
        # **last** pipe, which in a markdown row is the empty string, so every row read
        # as having no status and the whole check reported nothing. A guard silent
        # because its parser is broken is worse than no guard: it is a green light.
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        section = cells[0]
        if not re.fullmatch(r"[0-9]+\.[0-9]+|[A-Z][0-9]?", section):
            continue
        reqs = by_section.get(section)
        if not reqs:
            continue
        built = [r for r in reqs if not r.not_built]

        # Every bold span in the row except the section id. Which column the status sits
        # in differs between the document's tables -- third of four in some, last in
        # others -- so the column is not what identifies it.
        bold = [b.strip().strip(".").lower()
                for cell in cells[1:] for b in _BOLD.findall(cell)]
        says_nothing = any(b in _NOTHING_WORDS for b in bold)
        says_built = any(b in _BUILT_WORDS for b in bold)

        if says_nothing and len(built) == len(reqs):
            problems.append(
                f"docs/requirements.md:{number} says section {section} is absent and all "
                f"{len(reqs)} of its requirements are built: "
                f"{[r.id for r in built]}"
            )
        elif says_nothing and built:
            problems.append(
                f"docs/requirements.md:{number} says section {section} is absent and "
                f"{len(built)} of its {len(reqs)} requirements are built: "
                f"{[r.id for r in built]}. Partial is the honest word"
            )
        elif says_built and not built:
            problems.append(
                f"docs/requirements.md:{number} says section {section} is built and none of "
                f"its {len(reqs)} requirements are: {[r.id for r in reqs]}"
            )
    return problems


def check() -> list[str]:
    return _layout_problems() + _status_problems()


def main() -> int:
    problems = check()
    if not problems:
        print("the documents agree with the code")
        return 0
    print(f"{len(problems)} place(s) where a document no longer describes this codebase:")
    for problem in problems:
        print(f"  - {problem}")
    print()
    print("A document saying something is absent after it was built sends the next "
          "reader to build it again. Prose does not fail on its own, which is why this "
          "exists.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
