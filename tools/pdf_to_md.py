"""Convert the Notion-exported plan PDFs in docs/originals/ to Markdown.

Headings come from the font size the exporter used (32 title, 22 section, 18
subsection); tables from pypdf's layout extraction, which keeps columns aligned at
fixed offsets; inline code from the mono font. Page furniture -- the running footer
and "Page n of m" -- is dropped.

Two things the PDF does not carry in its text and this therefore reconstructs:

- **Paragraph wrapping and bullet markers.** The PDF stores one line per visual
  line, and blank lines separate the blocks. Inside a block the shape decides, and
  only one case is guessed: an unindented block is one paragraph and a numbered item
  owns every indented line beneath it, both exactly, because those markers are real
  text; but Notion draws *bullet* markers as vector paths, so nothing survives export
  to separate one item from the next. There, a line stopping short of the right
  margin is taken to end its item. The guess is confined to that case, and the
  numbered lists -- same geometry, real markers -- are what it was checked against.
- **Inline code.** The mono font is known per span, but layout extraction gives text
  without spans, so the mono strings are matched back by name. Only strings that
  cannot be ordinary English are wrapped -- ones holding ``_ / . { } ( )`` or a digit.
  A bare word like ``setup`` is left alone: it is set in mono in the PDF, but marking
  every later use of the English word as code would make a claim the document does not.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

SIZE_TO_LEVEL = {32.0: 1, 22.0: 2, 18.0: 3}
FOOTER_SIZE = 8.0
#: The running footer of the architecture document is its Chinese title. Written as
#: ranges rather than as characters, because the language guard reads this file too.
CJK = re.compile("[\u3000-\u9fff\uff00-\uffef]")
GAP = re.compile(r"\S {3,}(?=\S)")
CODEISH = re.compile(r"[_/{}()\.]|\d")
#: The print fonts set these as single glyphs; the language guard counts them as
#: letters that are not English, and they break a grep for the word either way.
LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
             "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"}
#: Body list items are laid out at this indent; the byline sits at two, and is not
#: a list.
LIST_INDENT = 3


# --------------------------------------------------------------------------- spans

def page_spans(page) -> list[tuple[float, str, str]]:
    got: list[tuple[float, str, str]] = []

    def visit(text, cm, tm, font, size):
        t = text.strip()
        if not t:
            return
        name = str(font.get("/BaseFont", "")) if isinstance(font, dict) else ""
        kind = "mono" if "Mono" in name else "serif" if "Serif" in name else "sans"
        got.append((round(float(size), 1), kind, t))

    page.extract_text(visitor_text=visit)
    return got


def mono_blob(got) -> str:
    """All of a page's mono text, whitespace removed.

    A code block and a wide table look identical to layout extraction -- both are
    runs of aligned columns -- and splitting a code block into cells cuts words in
    half (``version`` became ``ve`` and ``rsion``). The font tells them apart: a
    code block's whole line is mono, while a table's cells are not. A line whose
    compacted text appears in here is a line of code."""
    return re.sub(r"\s+", "", "".join(t for _size, kind, t in got if kind == "mono"))


def fence(block: list[str]) -> list[str]:
    indent = min(len(l) - len(l.lstrip()) for l in block if l.strip())
    lines = [l[indent:].rstrip() for l in block]
    lang = "yaml" if sum(bool(re.match(r"\s*[\w_]+:", l)) for l in lines) * 2 >= len(lines) else ""
    return ["", "```" + lang, *lines, "```", ""]


def body_size(all_spans) -> float:
    """The size body prose is set at: the largest size that carries real text.

    Not the *most common* one -- a document with many wide tables sets more
    characters in table cells than in prose, and taking the mode then calls the
    table size "body" and every real table prose. Prose is the largest text on the
    page that is not a heading, so the largest size holding at least a twentieth of
    the characters is it."""
    weight = Counter()
    for got in all_spans:
        for size, kind, text in got:
            if kind != "serif":
                weight[size] += len(text)
    if not weight:
        return 16.0
    total = sum(weight.values())
    return max(size for size, n in weight.items() if n >= total * 0.05)


def small_words(got, body: float) -> set[str]:
    """The words a page sets below body size -- which is to say, its table cells.

    Prose with an inline mono run looks exactly like a table to layout extraction:
    the narrower font is padded out to the fixed columns, leaving wide gaps at
    arbitrary places. Reading such a paragraph as a table cuts words at the invented
    boundary (``listed`` became ``li`` and ``sted``). Size separates them -- a table
    is set smaller throughout, while a padded paragraph is body size but for the
    mono run itself."""
    out: set[str] = set()
    for size, _kind, text in got:
        if size < body - 0.5:
            out.update(w.lower() for w in re.findall(r"[\w/()-]{4,}", text))
    return out


def looks_tabular(line: str, words: set[str]) -> bool:
    """Is this line's text set small enough to be a table row rather than prose."""
    toks = [w.lower() for w in re.findall(r"[\w/()-]{4,}", line)]
    if not toks:
        return True
    return sum(t in words for t in toks) / len(toks) >= 0.6


def headings(got) -> dict[str, int]:
    """{heading text: level}. A heading split over spans is joined, and the parts
    are indexed too, since layout extraction may break the line differently."""
    out: dict[str, int] = {}
    run_size, parts = None, []

    def flush():
        if run_size in SIZE_TO_LEVEL and parts:
            joined = " ".join(parts).strip()
            if joined:
                out[joined] = SIZE_TO_LEVEL[run_size]
            for p in parts:
                out.setdefault(p.strip(), SIZE_TO_LEVEL[run_size])
        parts.clear()

    for size, _kind, text in got:
        if size != run_size:
            flush()
            run_size = size
        if size in SIZE_TO_LEVEL:
            parts.append(text)
    flush()
    return out


# -------------------------------------------------------------------------- tables

def tidy(text: str) -> str:
    """Collapse the padding layout extraction leaves around an inline mono run.

    A mono span is set in a narrower font, so laying the page out in fixed columns
    pads both sides of it. In a table those columns are the structure and must be
    kept; in prose they are an artefact, and the sentence has to read as one."""
    text = re.sub(r"\s{2,}", " ", text.strip())
    text = re.sub(r"\s+([,.;:!?)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    return re.sub(r"\s+'", "'", text)


def is_row(line: str) -> bool:
    return bool(line.strip()) and len(GAP.findall(line)) >= 1


def in_block(line: str) -> bool:
    """A line that continues a table block: blank, column-split, or an indented
    continuation of a cell (which on its own has no wide gap to show for it)."""
    return (not line.strip()) or is_row(line) or line.startswith("  ")


def columns(block: list[str]) -> list[int]:
    """The column offsets of a table block.

    A cell holding an inline mono run is padded out around it, which puts a wide gap
    in the middle of the cell and offers a column boundary that is not one. Such a
    boundary always cuts some other row's text in half -- that is what makes it
    wrong -- so a candidate is kept only where every row either has whitespace in
    front of it or ends before it. (``capital`` was being split into ``capita`` and
    ``l`` by exactly this.)"""
    rows = [l for l in block if l.strip()]
    votes = Counter()
    for line in rows:
        votes[len(line) - len(line.lstrip())] += 1
        for m in re.finditer(r"\s{3,}(?=\S)", line):
            votes[m.end()] += 1
    merged: list[int] = []
    for pos, _n in sorted((p, n) for p, n in votes.items() if n >= 2):
        if merged and pos - merged[-1] <= 2:
            continue
        if pos and any(len(l) > pos and l[pos - 1] != " " for l in rows):
            continue                                  # it would cut a word in half
        merged.append(pos)
    return merged


def as_table(block: list[str]) -> list[str] | None:
    while block and not block[-1].strip():
        block.pop()
    cols = columns(block)
    if len(cols) < 2:
        return None
    rows: list[list[str]] = []
    after_blank = True
    for line in block:
        if not line.strip():
            after_blank = True
            continue
        cells = []
        for i, start in enumerate(cols):
            end = cols[i + 1] if i + 1 < len(cols) else len(line)
            cells.append(line[start:end].strip())
        # Rows are separated by a blank line; a line without one continues the row
        # above, whichever of its cells carry text -- the first column wraps too,
        # and "Run"/"settings" was being read as two rows because of it.
        if after_blank or not rows:
            rows.append(cells)
        else:
            for i, c in enumerate(cells):          # wrapped continuation of a cell
                if c:
                    rows[-1][i] = (rows[-1][i] + " " + c).strip()
        after_blank = False
    if len(rows) < 2:
        return None
    esc = lambda c: tidy(c).replace("|", "\\|")
    width = len(cols)
    out = ["| " + " | ".join(esc(c) for c in rows[0]) + " |",
           "|" + "|".join([" --- "] * width) + "|"]
    out += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows[1:]]
    return out


# --------------------------------------------------------------------------- prose


def unwrap(lines: list[str], wrap_at: int) -> list[str]:
    """Join visual lines into paragraphs.

    Blank lines delimit blocks, and within a block the shape decides:

    - a numbered item begins at the left margin with its marker, and every indented
      line under it continues it. Exact -- the markers are real text.
    - an unindented block with no markers is one paragraph. Exact.
    - an indented block is a bullet list, and only here is the boundary guessed:
      Notion draws bullet markers as vector paths, so nothing survives extraction to
      separate the items. A line stopping well short of the right margin ends its
      item. The guess is confined to this case, and is checkable against the numbered
      lists, which have the same geometry and do carry their markers.
    """
    out: list[str] = []
    block: list[str] = []

    def emit_block():
        if not block:
            return
        indented = all(l.startswith(" " * LIST_INDENT) for l in block)
        numbered = any(re.match(r"\d+\.\s", l.strip()) for l in block)
        items: list[str] = []
        for n, line in enumerate(block):
            bare = tidy(line)
            if not items:
                starts = True
            elif numbered:
                starts = bool(re.match(r"\d+\.\s", bare))
            elif indented:
                starts = len(block[n - 1].rstrip()) < wrap_at
            else:
                starts = False
            if starts:
                items.append(bare)
            else:
                items[-1] += " " + bare
        prefix = "- " if indented and not numbered else ""
        out.extend(prefix + it for it in items)
        block.clear()

    for line in lines:
        if not line.strip():
            emit_block()
            out.append("")
        else:
            block.append(line)
    emit_block()
    return out


def wrap_width(all_lines: list[str]) -> int:
    """The length at or above which a visual line is a wrapped continuation.

    Measured from body prose only: unindented, no wide gap, long enough to have
    been wrapped at all. The widest such line is the right margin; a line within
    ten columns of it was broken by the margin rather than by its author."""
    lens = [len(l.rstrip()) for l in all_lines
            if l.strip() and not is_row(l) and not l.startswith("  ")
            and len(l.rstrip()) > 60]
    return (max(lens) - 14) if lens else 74


# ------------------------------------------------------------------------- convert

HEADER = """<!-- Generated by tools/pdf_to_md.py from {source}. Do not edit by hand:
     regenerate instead, or the two accounts of the plan drift apart. -->

> **Converted from `{source}`.** The PDF is the original; this is here so the plan can
> be read, searched and quoted without opening it.
>
> What the conversion cannot carry, and you should go to the PDF for: **the {ndiag}
> diagram{plural}** (marked in place below), and the exact page breaks. Two things are
> reconstructed rather than read, because the PDF does not store them: **paragraph
> wrapping** (a line is joined to the next unless it stops short of the right margin)
> and **bullet markers** (Notion draws them as shapes, so no character survives
> export -- numbered lists keep their real markers). Headings, tables and code blocks
> come from the fonts the exporter used, so they are read rather than guessed.

"""


def _images(page) -> int:
    try:
        return len(list(page.images))
    except Exception:
        return 0


def stitch(lines: list[str]) -> list[str]:
    """Rejoin a table the page break cut in two.

    Each page is laid out on its own, and a table continued on the next one has its
    header printed again there. Converted page by page, that reads as two tables --
    and half a table presented as a whole one is the kind of untruth a document
    should not tell. Where two tables are separated by nothing but blank lines and
    their header rows are identical, they are one table, and the second header goes.

    Nothing else is removed: every line this drops is a repeated header or its rule.
    """
    blocks: list[tuple[int, int]] = []          # the [start, end) of each table
    i = 0
    while i < len(lines):
        if lines[i].startswith("|"):
            j = i
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            blocks.append((i, j))
            i = j
        else:
            i += 1

    drop: set[int] = set()
    for a, b in zip(blocks, blocks[1:]):
        if any(lines[k].strip() for k in range(a[1], b[0])):
            continue                            # something real sits between them
        if b[1] - b[0] < 3 or lines[a[0]] != lines[b[0]]:
            continue
        if not lines[b[0] + 1].startswith("| --- "):
            continue
        drop.update({b[0], b[0] + 1})
        drop.update(k for k in range(a[1], b[0]))
    return [l for k, l in enumerate(lines) if k not in drop]


def convert(pdf: Path, out_md: Path, source: str) -> None:
    reader = PdfReader(str(pdf))
    pages = list(reader.pages)
    spans = [page_spans(p) for p in pages]
    heads = [headings(s) for s in spans]
    monos = [mono_blob(s) for s in spans]
    body = body_size(spans)
    smalls = [small_words(s, body) for s in spans]
    footers = {t for s in spans for size, _k, t in s if size == FOOTER_SIZE}
    code = {t for s in spans for _size, k, t in s
            if k == "mono" and len(t) > 2 and CODEISH.search(t)}

    layouts = [p.extract_text(extraction_mode="layout").splitlines() for p in pages]
    wrap_at = wrap_width([l for ls in layouts for l in ls])

    body: list[str] = []
    for i, raw in enumerate(layouts):
        lines = []
        for line in raw:
            bare = line.strip()
            if CJK.search(bare) or re.fullmatch(r"Page \d+ of \d+", bare):
                continue
            if bare and bare in footers:
                continue
            lines.append(line.rstrip())

        nimg = _images(pages[i])
        caption = ""
        if nimg:
            for j, line in enumerate(lines):
                bare = line.strip()
                if " · " in bare and len(bare) < 70 and bare not in heads[i]:
                    caption = bare
                    lines.pop(j)
                    break

        out: list[str] = []
        buf: list[str] = []
        prose: list[str] = []
        codebuf: list[str] = []
        blanks = 0

        def is_code(line: str) -> bool:
            compact = re.sub(r"\s+", "", line)
            return len(compact) >= 3 and compact in monos[i]

        def flush_code():
            if not codebuf:
                return
            while codebuf and not codebuf[-1].strip():
                codebuf.pop()
            if len(codebuf) >= 2:
                out.extend(fence(list(codebuf)))
            else:
                prose.extend(codebuf)
            codebuf.clear()

        def flush_prose():
            if prose:
                out.extend(unwrap(prose, wrap_at))
                prose.clear()

        def flush_table():
            if not buf:
                return
            table = as_table(list(buf)) if sum(map(is_row, buf)) >= 2 else None
            if table:
                out.extend(["", *table, ""])
            else:
                prose.extend(buf)
                flush_prose()
            buf.clear()

        for line in lines:
            bare = line.strip()
            blanks = blanks + 1 if not bare else 0
            level = heads[i].get(bare)
            if level:
                flush_code()
                flush_table()
                flush_prose()
                # A heading that wrapped arrives as two lines, each matching one
                # part of it. Rejoin them when the pair is the heading we indexed.
                prev = out[-2] if len(out) >= 2 and out[-1] == "" else ""
                hashes = "#" * level + " "
                if prev.startswith(hashes) and (
                        prev[len(hashes):] + " " + bare) in heads[i]:
                    out[-2] = hashes + prev[len(hashes):] + " " + bare
                else:
                    out.extend(["", hashes + bare, ""])
            elif is_code(line):
                flush_table()
                flush_prose()
                codebuf.append(line)
            elif codebuf and not bare and blanks < 2:
                codebuf.append(line)
            elif codebuf:
                flush_code()
                prose.append(line)
            elif buf and in_block(line) and blanks < 2:
                buf.append(line)
            elif is_row(line) and looks_tabular(line, smalls[i]):
                flush_prose()
                buf.append(line)
            else:
                flush_table()
                prose.append(line)
        flush_code()
        flush_table()
        flush_prose()

        if nimg:
            note = f"> **Diagram, page {i + 1} of the source PDF**"
            if caption:
                note += f" — *{caption}*"
            note += f"\n>\n> Not reproduced in this conversion; see `{source}`, page {i + 1}."
            out = ["", note, ""] + out
        body.append("\n".join(out))

    # Mark the mono strings as code -- outside the fenced blocks, which are code
    # already and would otherwise get a second pair of backticks inside them.
    out_lines, fenced = [], False
    for line in "\n".join(body).split("\n"):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            for c in sorted(code, key=len, reverse=True):
                line = re.sub(rf"(?<![`\w/.]){re.escape(c)}(?![`\w])",
                              lambda m, c=c: f"`{c}`", line)
        out_lines.append(line)
    text = "\n".join(stitch(out_lines))
    ndiag = sum(1 for p in pages
                for _ in [0] if _images(p))
    text = HEADER.format(source=source, ndiag=ndiag,
                         plural="" if ndiag == 1 else "s") + text
    for lig, plain in LIGATURES.items():
        text = text.replace(lig, plain)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    text = with_anchors(text)
    out_md.write_text(text, encoding="utf-8")
    print(f"{pdf.name}\n   -> {out_md.name}  ({len(text)} chars, wrap at {wrap_at})")


# ------------------------------------------------------------------------- anchors

#: A heading that carries a section designator, which is how the rest of the project
#: refers to this document: ``tools/check_plan.py`` holds the list of sections as
#: ``("0.1", "Events")``, ``("5.1", "Engine")``, ``("1", "Data")``, and criteria in
#: ``docs/requirements.md`` are numbered to match. Matches ``## Layer 5: ...`` and
#: ``### 5.1 ...``; everything else is left alone.
SECTION_HEADING = re.compile(r"^(#{1,6}) (?:Layer (\d+)\b|(\d+(?:\.\d+)+)(?=[\s.]))")


def with_anchors(text: str) -> str:
    """Put a stable anchor before every heading that carries a section number.

    ## Why the generated anchor and not the heading's own

    A Markdown renderer derives an anchor from the heading text, so section 5.1 becomes
    ``#51-engine``. That works until someone cites it, because the citation then carries
    the title as well as the number -- and a citation that has to spell the title is one
    that breaks when the title is reworded, which is the opposite of stable. It also
    cannot be written by anyone who knows only the number, which is the case for every
    citation from code and from ``docs/requirements.md``.

    ``<a id="section-5-1"></a>`` is derivable from the number alone: section ``5.1`` is
    ``docs/architecture.md#section-5-1``, ``Layer 1`` is ``#section-1``. The numbers come
    from the PDF and the PDF is frozen, so the anchors do not move.

    Emitted here, in the generator, rather than added to the Markdown -- the file says at
    the top not to edit it by hand, and hand-added anchors would be gone at the next
    regeneration with nothing raising.
    """
    out: list[str] = []
    for line in text.splitlines():
        m = SECTION_HEADING.match(line)
        if m:
            number = m.group(2) or m.group(3)
            out.append(f'<a id="section-{number.replace(".", "-")}"></a>')
        out.append(line)
    return "\n".join(out) + "\n"


#: Where the PDFs are read from. They are the originals, and they sit beside the
#: transcriptions rather than in a directory of their own at the root.
#:
#: **This default was changed without the generator being re-run.** Its output on the
#: machine where the layout was tidied differed from the committed transcriptions by more
#: than the header -- figure placeholders and table placement moved -- so regenerating
#: would have buried three path corrections inside an unreviewable diff of a generated
#: document. The citation strings inside the three Markdown files were corrected by hand
#: instead, and this default is correct for the next deliberate regeneration.
DEFAULT_SRC = "docs/originals"

#: PDF stem -> Markdown name, without the suffix. The architecture document is
#: ``architecture`` rather than its own file name: it is the one the code and the other
#: documents cite, and ``docs/architecture.md`` is the path they cite it by.
NAMES = {
    "backtest-system-architecture": "architecture",
    "frontend-web-plan": "frontend-web-plan",
    "ui-ux-design-plan-addendum": "ui-ux-design-plan-addendum",
}

#: Where the conversions go. ``docs/originals/`` holds the PDFs, which are the
#: originals; the
#: Markdown is a derived document and belongs with the documents. Defaulting to ``docs``
#: rather than the directory the PDFs used to sit in is what keeps regeneration
#: landing where the committed files are
#: -- a generator whose default writes somewhere else produces a second copy, and then
#: two accounts of the plan drift apart, which is what the header of each file warns of.
DEFAULT_DST = "docs"

if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC)
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DST)
    for pdf in sorted(src.glob("*.pdf")):
        # The citation stamped into the generated header. Normalised rather than
        # taken from argv as given: `python tools/pdf_to_md.py $PWD/docs/originals`
        # would otherwise write an absolute machine path into three documents, and
        # the next reader would see three headers change and read it as the
        # documents having changed.
        citation = Path(os.path.relpath(src / pdf.name, Path.cwd())).as_posix()
        convert(pdf, dst / (NAMES[pdf.stem] + ".md"), citation)
