"""The code in the docs has to run.

## Why this exists

Code in a document that does not run is worse than no document — it looks
authoritative. And documents rot inevitably: an interface changes, a parameter is
renamed, a module moves, and the document does not follow.

Hand-written static descriptions rot the same way: they were true when written and the
code moved on.

Only the **executable part** is checked: the Python blocks in the README. A block
whose **first non-empty line** starts with ``# skip`` is skipped (an example that
needs a real data file, say). A block that does not run means the document is wrong,
or the code changed without it.

## Why the marker has to be on the first line

The rule used to be ``"# skip" in code`` — the marker anywhere in the block. That is
not a rule about the block, it is a rule about any line in it, and the difference let
a whole block go unchecked by accident. The README's data-adapter example was skipped
because a **commented-out line four lines down** happened to carry ``# skip``:

    from backtest.data.adapters import check_file, describe_schema

    print(describe_schema())
    # check_file("your/path")   # 2. a health check before any run  # skip

Its first line imports ``backtest.data.adapters``, a module that does not exist — the
adapters live in ``backtest.data.options.adapters``. So the one block in the README
whose import was wrong was also the one block the guard never ran, and it had been
wrong long enough for nobody to notice. An exemption that can be granted from a
comment deep inside a block is an exemption granted by accident.

Putting the marker on the first line makes skipping a property of the block, declared
where a reader of the document sees it, and makes a trailing ``# skip`` on some inner
line mean nothing at all.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ("README.md",)
#: An example needing something external (your data file, a network) is skipped with
#: this marker, **on the block's first non-empty line**. See the module docstring for
#: what a marker anywhere cost.
SKIP_MARK = "# skip"


def blocks(text: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", text, re.S)


def is_skipped(code: str) -> bool:
    """Whether this block declares itself unrunnable, on its first non-empty line."""
    first = next((line for line in code.splitlines() if line.strip()), "")
    return first.strip().startswith(SKIP_MARK)


def main() -> int:
    failures: list[tuple[str, int, str]] = []
    total = 0
    for name in DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        for i, code in enumerate(blocks(path.read_text(encoding="utf-8")), 1):
            if is_skipped(code):
                continue
            total += 1
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                             encoding="utf-8") as fh:
                fh.write(code)
                tmp = fh.name
            r = subprocess.run([sys.executable, tmp], cwd=ROOT,
                               capture_output=True, text=True, timeout=300)
            Path(tmp).unlink(missing_ok=True)
            if r.returncode != 0:
                tail = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
                failures.append((name, i, tail))

    if failures:
        print(f"the doc-code check failed ({len(failures)}/{total} blocks did not run)")
        for name, i, err in failures:
            print(f"  - {name}, python block {i}: {err}")
        print("\nCode in a document that does not run is worse than no document — it "
              "looks authoritative.")
        return 1
    print(f"the doc-code check passed ({total} blocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
