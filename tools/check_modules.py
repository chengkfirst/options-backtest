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



#: What may sit immediately before a `/` that opens a regex rather than dividing. After a
#: value — a name, a number, a closing bracket — a slash is division; after an operator, a
#: comma, or the start of a statement, it opens a pattern. This is the standard heuristic
#: and it is enough here, because the file is read for identifiers rather than evaluated.
_BEFORE_REGEX = set("(,=:[!&|?{};+-*%~^<>") | {"\n"}


def _is_regex_here(src: str, i: int) -> bool:
    """Whether the `/` at ``i`` opens a regex literal."""
    j = i - 1
    while j >= 0 and src[j] in " \t":
        j -= 1
    if j < 0:
        return True
    if src[j] in _BEFORE_REGEX:
        return True
    # `return /x/` and `typeof /x/` — a keyword, not a value.
    word = ""
    k = j
    while k >= 0 and (src[k].isalnum() or src[k] == "_"):
        word = src[k] + word
        k -= 1
    return word in {"return", "typeof", "case", "in", "of", "new", "delete", "void",
                    "instanceof", "do", "else", "yield", "await"}


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
        elif src[i] == "/" and _is_regex_here(src, i):
            # A regex literal. Without this the scanner reads `/'/` as a division followed
            # by an opening quote, and the runaway string swallows the rest of the file —
            # which is how `esc`'s own `.replace(/'/g, …)` made this guard report six live
            # exports as missing. The slashes and the body are blanked; a regex contains no
            # identifier the import check cares about.
            j = i + 1
            in_class = False
            while j < len(src):
                c = src[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "[":
                    in_class = True
                elif c == "]":
                    in_class = False
                elif c == "/" and not in_class:
                    j += 1
                    break
                elif c == "\n":
                    break          # an unterminated regex: not one after all
                j += 1
            while j < len(src) and src[j].isalpha():
                j += 1             # the flags
            out.append(re.sub(r"[^\n]", " ", src[i:j]))
            i = j
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
                # **Comments first.** A hole holds real code, and real code holds comments,
                # and a comment holds prose — prose with apostrophes in it. Skipping
                # strings but not comments meant that `// the parameter's kind` opened a
                # quote that ran to the next apostrophe in the file, swallowing 21,000
                # characters of code and reporting three imports as unused.
                if src[j:j + 2] == "//":
                    k = src.find("\n", j)
                    j = n if k < 0 else k
                    continue
                if src[j:j + 2] == "/*":
                    k = src.find("*/", j + 2)
                    j = n if k < 0 else k + 2
                    continue
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


def namespace_imports(src: str) -> list[tuple[str, str]]:
    """``import * as name from "path"`` — the local name and the path.

    A separate form, and the one the shell uses to reach a view. Its names cannot be
    checked against the target's exports (the whole module is bound), but its **path**
    can, and a path that does not resolve is the failure that matters: the browser logs
    one line and the surface never appears.
    """
    return re.findall(r"import\s+\*\s+as\s+([A-Za-z_$][\w$]*)\s+from\s*[\"']([^\"']+)[\"']",
                      src)


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


#: Shapes that have each broken the scanner once, with what must survive them.
#:
#: The scanner is the whole guard: if it loses its place, everything after the construct
#: that lost it is blanked, and the check then reports imports as unused and calls as
#: missing with complete confidence. That has happened three times — `\b` never matching
#: before `$`, template holes blanked along with their text, and an apostrophe in a comment
#: **inside** a hole opening a string that ran for 21,000 characters. Each one looked like
#: a finding about the code rather than a bug in the reader.
#:
#: So the reader is checked before it is believed.
PARSER_CASES: tuple[tuple[str, str, bool], ...] = (
    # The real shape: an apostrophe in a comment inside a hole, and **another apostrophe
    # further down the file**. The first opens a bogus string, the second closes it, and
    # everything between — including the end of the template — is swallowed, so the code
    # after the template is read as template text and blanked. A case with only one
    # apostrophe does not reproduce it: the runaway string reaches the end of the input and
    # the recursive pass over the hole still sees the code.
    ("an apostrophe in a comment inside a hole",
     "const a = `x${ fn(() => {\n  // the parameter's kind\n  return 1;\n}) }`;\n"
     "// it's later\nconst b = SURVIVES(1);",
     True),
    ("a block comment inside a hole",
     "const a = `x${ fn(() => { /* it's fine */ return SURVIVES(2); }) }`;", True),
    ("code inside a hole is kept", "const a = `x${ SURVIVES(3) }y`;", True),
    ("a nested template inside a hole",
     "const a = `x${ c ? `y${ SURVIVES(4) }z` : '' }w`;", True),
    ("text inside a template is dropped", "const a = `SURVIVES is only text here`;", False),
    ("a line comment is dropped", "// SURVIVES\nconst a = 1;", False),
    ("a plain string is dropped", 'const a = "SURVIVES";', False),
    # A quote inside a regex literal. `.replace(/'/g, "&#39;")` in dom.js read as a
    # division and an opening string, and the runaway swallowed every export after it —
    # the guard then reported six names as not exported while they sat in the file.
    ("a quote inside a regex",
     'const a = s.replace(/\'/g, "x");\nconst b = SURVIVES(5);', True),
    ("a slash inside a character class",
     'const a = s.split(/[/]/);\nconst b = SURVIVES(6);', True),
    ("division is not a regex", "const a = (x) / y; const b = SURVIVES(7);", True),
)


def check_parser() -> list[str]:
    """Verify the scanner on the shapes that have broken it, before trusting its output."""
    wrong = []
    for name, source, should_survive in PARSER_CASES:
        survived = "SURVIVES" in strip_noise(source)
        if survived is not should_survive:
            wrong.append(
                f"the source reader is broken: with {name}, code that should "
                f"{'survive' if should_survive else 'be blanked'} was "
                f"{'blanked' if should_survive else 'kept'}. Every finding below would be "
                f"unreliable, so none is reported"
            )
    return wrong


def main() -> int:
    broken = check_parser()
    if broken:
        print("the module check could not run:")
        for line in broken:
            print(f"  - {line}")
        return 1
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
        for local, path in namespace_imports(src):
            if not (f.parent / path).resolve().exists():
                problems.append(f"{here} imports * as {local} from {path}, and there is "
                                f"no such file")
            own.add(local)
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
