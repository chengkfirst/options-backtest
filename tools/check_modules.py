"""The interface's modules resolve: every import exists, and nothing uses what it did not
import.

## The failure this prevents

There is no bundler and no build step — the browser loads the modules directly — so a
missing import raises **only when the code path that needs it runs**. A chart that throws
the first time somebody groups by a particular column is a silent failure until that
somebody does it, and the page by then shows a blank panel with a line in the problem
strip rather than anything about an import.

It is not hypothetical: splitting ``charts.js`` into modules left ``distribution.js``
calling ``quantile`` and ``tables.js`` calling ``notice`` with neither imported. Both
files loaded fine. Both would have thrown on use.

## The three rules

1. **Every import resolves.** The file exists and really exports that name. This is what
   catches a rename on the other side — the same class of error as the front/back
   contract, one layer in.
2. **Nothing uses a project name it did not import.** Restricted to names some module
   exports, so browser and language builtins are out of scope and there is nothing to
   maintain as an allowlist.
3. **No import goes unused.** A leftover from a move claims a dependency that is not
   real, and the next reader trusts the import list to say what a module needs.

Comments and string literals are removed before anything is scanned, because the word
``notice`` in a sentence is not a call, and the first version of this check reported one.
The code inside a template literal's ``${...}`` is **kept** — this interface builds its
markup out of template literals, so most of its calls live in one.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "app" / "static" / "js"


def strip_noise(src: str) -> str:
    """Source with comments and string literals blanked out, **keeping the code inside a
    template literal's** ``${...}``.

    Blanked rather than deleted, so nothing on either side of a removed string becomes
    accidentally adjacent and reads as one identifier. Newlines are kept for the same
    reason.

    The interpolations matter more here than anywhere: this interface builds its markup
    out of template literals, so most calls in it sit inside one. A first version blanked
    them with the rest of the string and reported `label`, `pct` and `REASON` as unused
    imports in files that call them on every render — a guard confidently wrong about the
    code it was reading.
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        two = src[i:i + 2]
        if two == "//":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", src[i:j]))
            i = j
        elif src[i] == "`":
            text, i = _template(src, i)
            out.append(text)
        elif src[i] in "\"'":
            quote = src[i]
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(re.sub(r"[^\n]", " ", src[i:j]))
            i = j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def _template(src: str, start: int) -> tuple[str, int]:
    """One template literal, blanked except for the code in its ``${...}`` holes.

    Returns the replacement text and the index just past the closing backtick. Nested
    templates inside a hole go through :func:`strip_noise` again, which is how
    ``` `${rows.map(r => `<td>${fmt(r)}</td>`)}` ``` keeps both ``map`` and ``fmt``.
    """
    out = [" "]                        # the opening backtick
    i, n = start + 1, len(src)
    while i < n:
        if src[i] == "\\":
            out.append("  ")
            i += 2
            continue
        if src[i] == "`":
            out.append(" ")
            return "".join(out), i + 1
        if src[i:i + 2] == "${":
            depth, j = 1, i + 2
            while j < n and depth:
                if src[j] == "{":
                    depth += 1
                elif src[j] == "}":
                    depth -= 1
                    if not depth:
                        break
                elif src[j] in "\"'`":
                    # A string inside the hole: skipped here and blanked by the recursive
                    # call below, so its contents cannot close the hole early.
                    quote, j = src[j], j + 1
                    while j < n:
                        if src[j] == "\\":
                            j += 2
                            continue
                        if src[j] == quote:
                            break
                        j += 1
                j += 1
            hole = src[i + 2:j]
            out.append("  " + strip_noise(hole) + " ")
            i = j + 1
            continue
        out.append("\n" if src[i] == "\n" else " ")
        i += 1
    return "".join(out), i


def exports(src: str) -> set[str]:
    clean = strip_noise(src)
    found = set(re.findall(r"export\s+(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)",
                           clean))
    for block in re.findall(r"export\s*\{([^}]*)\}", clean):
        for part in block.split(","):
            name = part.strip().split(" as ")[-1].strip()
            if name:
                found.add(name)
    return found


def imports(src: str) -> list[tuple[set[str], str]]:
    """Each import's names and the path it came from."""
    clean = strip_noise(src)
    out = []
    # The path survives in the stripped source only as blanks, so the paths are read from
    # the original and the names from the clean copy, pairing them by order.
    paths = re.findall(r"import\s*\{[^}]*\}\s*from\s*[\"']([^\"']+)[\"']", src)
    names = re.findall(r"import\s*\{([^}]*)\}\s*from", clean)
    for block, path in zip(names, paths):
        got = {p.strip().split(" as ")[-1].strip() for p in block.split(",") if p.strip()}
        out.append((got, path))
    return out


def defined(src: str) -> set[str]:
    clean = strip_noise(src)
    names = set(re.findall(
        r"(?:export\s+)?(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)", clean))
    # Destructured bindings and function parameters: taken loosely, because the cost of
    # missing one is a false positive and the cost of being strict is noise.
    names |= set(re.findall(r"(?:const|let|var)\s*\{([^}]*)\}", clean)) and set(
        n.strip().split(":")[-1].strip()
        for block in re.findall(r"(?:const|let|var)\s*\{([^}]*)\}", clean)
        for n in block.split(",") if n.strip())
    for params in re.findall(r"function\s*[A-Za-z_$][\w$]*\s*\(([^)]*)\)", clean):
        names |= {p.strip().split("=")[0].strip() for p in params.split(",") if p.strip()}
    for params in re.findall(r"\(([^)]*)\)\s*=>", clean):
        names |= {p.strip().split("=")[0].strip() for p in params.split(",") if p.strip()}
    for one in re.findall(r"([A-Za-z_$][\w$]*)\s*=>", clean):
        names.add(one)
    for caught in re.findall(r"catch\s*\(\s*([A-Za-z_$][\w$]*)", clean):
        names.add(caught)
    for loop in re.findall(r"for\s*\(\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)", clean):
        names.add(loop)
    return names


def main() -> int:
    if not JS.exists():
        print(f"there is no module directory at {JS.relative_to(ROOT)}")
        return 1
    files = sorted(JS.rglob("*.js"))
    if not files:
        print(f"no modules found under {JS.relative_to(ROOT)}")
        return 1

    source = {f: f.read_text(encoding="utf-8") for f in files}
    exported = {f: exports(src) for f, src in source.items()}
    #: Every name any module offers. A name used but not imported is only reported when
    #: some module exports it, which keeps browser and language builtins out of scope.
    offered: dict[str, list[str]] = {}
    for f, names in exported.items():
        for name in names:
            offered.setdefault(name, []).append(str(f.relative_to(JS)))

    problems: list[str] = []
    for f, src in source.items():
        here = str(f.relative_to(JS))
        own = defined(src)
        got: set[str] = set()
        for names, path in imports(src):
            target = (f.parent / path).resolve()
            if not target.exists():
                problems.append(f"{here} imports from {path}, and there is no such file")
                continue
            missing = names - exported.get(target, set())
            if missing:
                problems.append(
                    f"{here} imports {', '.join(sorted(missing))} from {path}, which "
                    f"does not export {'it' if len(missing) == 1 else 'them'}"
                )
            got |= names
        # The import statements themselves are removed first. Left in, every imported name
        # counts as a mention of itself, and the unused-import rule below can never fire —
        # it was written that way at first and was quietly dead.
        clean = re.sub(r"import\s*\{[^}]*\}\s*from\s*[^\n;]*;?", "", strip_noise(src))
        # Used as a call, a property owner, or an element access: enough to catch a
        # helper, which is what goes missing.
        # A dot before the name makes it a member call -- `bench.init()` and
        # `text.replace()` are not uses of an imported `init` or `replace`.
        used = set(re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*[\(\[]", clean))
        for name in sorted((used & set(offered)) - got - own):
            problems.append(
                f"{here} uses {name}() without importing it; "
                f"{' or '.join(offered[name])} exports it"
            )
        # An import nobody uses is a leftover from a move. Harmless to the browser and
        # worth removing anyway: it claims a dependency that is not real, and the next
        # reader trusts the import list to say what a module needs. Any mention at all
        # counts as use, so a name referenced without parentheses is not a false alarm.
        mentioned = set(re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)", clean))
        for name in sorted(got - mentioned - {"*"}):
            problems.append(f"{here} imports {name} and never uses it")

    if problems:
        print("the module check failed:")
        for p in problems:
            print(f"  - {p}")
        print("\nWith no bundler, a missing import raises only when that code path runs.")
        return 1
    print(f"the module check passed ({len(files)} modules · "
          f"{sum(len(v) for v in exported.values())} exported names)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
