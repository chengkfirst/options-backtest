"""Language guard: the whole project is written in English.

Mixed-language code fails silently in a specific way. A non-English dict key is a
*functional identifier* — the front end reads one spelling of it and a test asserts on
another. Rename one side and nothing raises: the lookup returns
``undefined``/``KeyError`` deep inside rendering, or worse, a ``.get()`` quietly
yields ``None`` and a metric reads as zero. Encoding, normalisation (NFC vs NFD),
and full-width punctuation each add a way for two spellings of "the same" key to
stop matching.

So the rule is not stylistic. One language means one spelling for every
identifier, and ``grep`` finds every use of it.

Scope: every text file the project ships. Non-ASCII that is *not* language —
mathematical symbols, box-drawing, arrows — is allowed; see ``ALLOWED``.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Extensions that carry prose or identifiers and must be English.
SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".json", ".toml", ".cfg",
            ".txt", ".yml", ".yaml", ".ipynb", ".sh"}

#: Directories that are not ours to rewrite.
SKIP_DIRS = {".git", ".venv", "__pycache__", ".ipynb_checkpoints", "node_modules",
             ".pytest_cache", "out"}

#: Non-ASCII characters that are *not* natural language and stay allowed:
#: typography, maths, box drawing, arrows, and the status glyphs the CLI prints.
#: Greek letters used as mathematical symbols (delta, sigma) are not language either, and
#: neither is an accent on a proper name — "López de Prado" is a citation in English text,
#: not a second language. The accented Latin letters are listed rather than allowed by
#: range, so adding one stays a deliberate act.
ALLOWED = set("·—–…×÷±≈≤≥≠∞√∑→←↑↓⇒│─└├┌┐┘•◦○●✓✗⚠✅⚠️“”‘’°%‰"
              "ΔΓΘΛΞΠΣΦΨΩαβγδεζηθικλμνξπρσςτυφχψω"
              "áàâäãéèêëíìîïóòôöõúùûüñçßÁÀÂÄÃÉÈÊËÍÌÎÏÓÒÔÖÕÚÙÛÜÑÇ")


def _offending(text: str) -> list[tuple[int, str]]:
    """Return ``(line number, the offending characters)`` for each bad line."""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        bad = ""
        for ch in line:
            if ord(ch) < 128 or ch in ALLOWED:
                continue
            # Letters and ideographs are language; symbols and marks are not.
            if unicodedata.category(ch)[0] in {"L", "P"}:
                bad += ch
        if bad:
            out.append((lineno, bad))
    return out


def _files() -> list[Path]:
    found: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        found.append(path)
    return sorted(found)


def check() -> list[str]:
    problems: list[str] = []
    for path in _files():
        rel = path.relative_to(ROOT)
        # A non-English *file name* is as much a breakage as a non-English key:
        # imports, shell quoting and URLs all have to spell it the same way.
        if _offending(str(rel)):
            problems.append(f"non-English file name: {rel}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"not valid UTF-8: {rel}")
            continue
        for lineno, bad in _offending(text):
            problems.append(f"{rel}:{lineno}  {bad}")
    return problems


def main() -> int:
    problems = check()
    if not problems:
        print(f"OK  {len(_files())} files, all English")
        return 0
    shown = problems[:40]
    for p in shown:
        print(f"  {p}")
    if len(problems) > len(shown):
        print(f"  ... and {len(problems) - len(shown)} more")
    print(f"\nFAIL  {len(problems)} non-English lines. "
          "The project is written in English; see conventions.md.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
