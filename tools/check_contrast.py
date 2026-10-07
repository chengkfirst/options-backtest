"""The interface's colours are legible, in both themes.

## The failure this prevents

A colour pair that is too close is a **silent** failure of the kind this project refuses:
the page renders, nothing raises, and a hint or an axis label simply cannot be read — and
whoever chose the colour had a bright screen that day and did not notice. It also cannot be
caught by review: nobody looks at ``#6b6b68`` on ``#111111`` and knows the ratio.

The design plan asks for it twice. Section 12: "Text contrast is at least 4.5 : 1."
Section 16 lists it as an acceptance criterion measured by an "automated check". And its
token table ends with: "Color values are a starting proposal; verify each against the
contrast, saturation and colour-blind rules in section 12 before finalizing." This is that
verification, so the proposal cannot be pasted in and called done.

## What it checks

- **text on surface, 4.5 : 1** (WCAG AA for body text), for every text level against every
  surface it can sit on, in both themes. ``--text-muted`` is included: a hint is text.
- **accent text, 4.5 : 1** — links and primary-button labels are text.
- **data colours, 3 : 1** (WCAG 1.4.11 for non-text) — a chart line nobody can see against
  the panel is a chart with no line.
- **separation under colour blindness**, enforced where colour is the only thing carrying
  which series is which — the comparison chart's lines, where two runs drawn alike is a
  wrong answer — and reported for the slots and the Greeks, which carry a number or a
  written label as well. The design plan's own proposed fourth series was 2.5 apart from
  its first, which is to say identical to a tenth of the men who would read it.

Values are read from ``app/static/css/tokens.css`` — the one file allowed to contain a
colour — so the check cannot drift from what the interface actually renders.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS = ROOT / "app" / "static" / "css" / "tokens.css"

#: WCAG AA: body text, and non-text such as a chart line or a control border.
TEXT_RATIO = 4.5
NON_TEXT_RATIO = 3.0

#: Below this, two colours are close enough that a colour-blind reader may not separate
#: them.
#:
#: **Enforced where colour is the only carrier of identity, reported everywhere else.**
#: A slot marker carries its number and an attribution bar carries its name, so a close
#: pair there is worth knowing about and is not by itself a defect. A line on a comparison
#: chart carries nothing but its colour, so a close pair there means two runs drawn alike —
#: and the design plan's own proposal had exactly that, at 2.5 apart.
SEPARATION = 18.0

#: The floor for a group where colour is the only carrier. Lower than the reporting
#: threshold, and the number is not arbitrary: four hues that each clear 3:1 on a
#: near-black **and** on a white ground, and separate under two kinds of colour blindness,
#: is an over-constrained problem. A search over every such colour puts the best a fourth
#: series can do at 22.7, and the design plan's own first three manage only 17.0 between
#: themselves — so a floor above that would condemn its palette rather than check it. The
#: colours clear this floor and a second carrier does the rest, which is why
#: `--dash-1..4` exist.
SOLE_CARRIER_FLOOR = 15.0

Rgb = tuple[float, float, float]


# ── reading the file ────────────────────────────────────────────────────────────
def _span(css: str, at: int) -> tuple[int, int]:
    """The extent of the block whose opening brace follows ``at``, by counting depth.

    Written rather than regexed. A pattern ending at ``\\n}`` matched the wrong brace the
    first time — indentation put two spaces before the inner one — and the whole
    light block was attributed to dark, so **dark was never checked at all** while the
    output claimed two themes had passed. A guard that silently checks the wrong thing is
    worse than no guard, so the brace matching is explicit.
    """
    open_at = css.index("{", at)
    depth = 0
    for i in range(open_at, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return open_at, i
    raise SystemExit("tokens.css has an unclosed block")


def _blocks(css: str) -> dict[str, dict[str, str]]:
    """The token declarations, by the theme they belong to.

    Matching the file's structure: the dark defaults on bare ``:root``, the system-light
    override inside the media query, and the explicit ``[data-theme="light"]`` block. A
    token declared only in the dark block is theme-independent (the data colours, spacing,
    type) and light inherits it. Comments are stripped first, or a hex inside one would be
    read as a declaration.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: dict[str, dict[str, str]] = {"dark": {}, "light": {}}

    def spans(*patterns: str) -> list[tuple[int, int]]:
        found = []
        for pattern in patterns:
            for m in re.finditer(pattern, css):
                found.append(_span(css, m.start()))
        return found

    light_spans = spans(r"@media \(prefers-color-scheme: light\)",
                        r':root\[data-theme="light"\]')
    # Recognised even though the file has none today: dark is the base. If one is ever
    # added, its declarations must not be read as theme-independent.
    dark_spans = spans(r"@media \(prefers-color-scheme: dark\)",
                       r':root\[data-theme="dark"\]')

    def within(pos: int, ranges: list[tuple[int, int]]) -> bool:
        return any(a <= pos <= b for a, b in ranges)

    for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", css):
        name, value = m.group(1), m.group(2).strip()
        if within(m.start(), light_spans):
            out["light"][name] = value
        elif within(m.start(), dark_spans):
            out["dark"][name] = value
        else:
            out["dark"][name] = value
            out["light"].setdefault(name, value)
    return out


# ── resolving a value to a colour ───────────────────────────────────────────────
def resolve(value: str, tokens: dict[str, str], backdrop: Rgb | None = None,
            depth: int = 0) -> Rgb | None:
    """A token's value as RGB, following `var()`, `rgba()` and `color-mix()`.

    ``backdrop`` is what a translucent colour sits on: ``rgba(255,255,255,0.08)`` is not a
    colour until something is behind it, and the borders are declared that way.
    """
    if depth > 8:
        return None
    value = value.strip()

    if m := re.fullmatch(r"var\((--[\w-]+)\)", value):
        nested = tokens.get(m.group(1))
        return resolve(nested, tokens, backdrop, depth + 1) if nested else None

    if m := re.fullmatch(r"#([0-9a-fA-F]{6})", value):
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]

    if m := re.fullmatch(r"rgba?\(([^)]+)\)", value):
        parts = [p.strip() for p in m.group(1).replace("/", ",").split(",")]
        channels = [float(p) / 255 for p in parts[:3]]
        alpha = float(parts[3]) if len(parts) > 3 else 1.0
        if alpha >= 1.0 or backdrop is None:
            return tuple(channels)  # type: ignore[return-value]
        return _over(tuple(channels), backdrop, alpha)  # type: ignore[arg-type]

    # color-mix(in srgb, A p%, B) — the form this file uses for the soft tints.
    if m := re.fullmatch(r"color-mix\(in srgb,\s*(.+?)\s+([\d.]+)%,\s*(.+?)\s*\)", value):
        a = resolve(m.group(1), tokens, backdrop, depth + 1)
        b = resolve(m.group(3), tokens, backdrop, depth + 1)
        if a is None or b is None:
            return None
        share = float(m.group(2)) / 100
        return _over(a, b, share)

    # `transparent` inside a mix means "take the other side, lightened by nothing" — the
    # only use here is the hatch, which is decorative and not checked.
    return None


def _over(top: Rgb, bottom: Rgb, alpha: float) -> Rgb:
    return tuple(top[i] * alpha + bottom[i] * (1 - alpha) for i in range(3))  # type: ignore[return-value]


# ── the measures ────────────────────────────────────────────────────────────────
def _channel(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: Rgb) -> float:
    r, g, b = (_channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: Rgb, b: Rgb) -> float:
    la, lb = luminance(a), luminance(b)
    lo, hi = sorted((la, lb))
    return (hi + 0.05) / (lo + 0.05)


def simulate(rgb: Rgb, kind: str) -> Rgb:
    """The colour as a protanope or deuteranope sees it (Viénot–Brettel–Mollon)."""
    r, g, b = rgb
    if kind == "protanopia":
        return (0.1121 * r + 0.8853 * g - 0.0005 * b,
                0.1127 * r + 0.8897 * g - 0.0001 * b,
                0.0045 * r + 0.0000 * g + 1.0019 * b)
    return (0.2920 * r + 0.7054 * g - 0.0003 * b,
            0.2934 * r + 0.7089 * g + 0.0000 * b,
            -0.0195 * r + 0.0333 * g + 0.9912 * b)


def separation(a: Rgb, b: Rgb) -> float:
    """How far apart two colours are, roughly perceptually, on a 0-100 scale."""
    return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5 / (3 ** 0.5) * 100


# ── the check ───────────────────────────────────────────────────────────────────
#: Which text levels must be legible on which surfaces. A text colour is checked against
#: every surface it can land on, because "it was only meant for the panel" is not a
#: property anything enforces.
TEXT_ON = {
    # The tinted surfaces are included because that is what renders on them: `.banner`,
    # `.issues` and `.anom.high` set a soft tint as the background and leave the text at
    # the body colour. Checking only the semantic colour on its own tint would have missed
    # the pair that carries the actual sentence.
    "--text-primary": ("--surface-0", "--surface-1", "--surface-2", "--surface-sunk",
                       "--accent-soft", "--success-soft", "--warning-soft",
                       "--danger-soft"),
    "--text-secondary": ("--surface-0", "--surface-1", "--surface-2", "--surface-sunk"),
    "--text-muted": ("--surface-0", "--surface-1", "--surface-2", "--surface-sunk"),
    # Link and primary-button text, and the tinted banners those colours also label.
    "--accent": ("--surface-0", "--surface-1", "--accent-soft"),
    "--success": ("--surface-1", "--success-soft"),
    "--warning": ("--surface-1", "--warning-soft"),
    "--danger": ("--surface-1", "--danger-soft"),
}

#: Non-text: a line, a bar, a marker. Drawn on the panel it lives in.
DATA_ON = ("--surface-1", "--surface-0")
DATA_TOKENS = ("--data-strategy", "--data-benchmark",
               "--slot-1", "--slot-2", "--slot-3", "--slot-4", "--slot-5", "--slot-6",
               "--slot-7",
               "--greek-delta", "--greek-gamma", "--greek-vega", "--greek-theta",
               "--greek-residual",
               "--leg-main", "--leg-hedge",
               "--data-1", "--data-2", "--data-3", "--data-4")

#: Groups where colour is the **only** carrier of which series is which. Enforced.
SOLE_CARRIER = {
    "comparison": ("--data-1", "--data-2", "--data-3", "--data-4"),
}

#: Groups that carry a number or a written label as well. Reported.
LABELLED = {
    "slot": ("--slot-1", "--slot-2", "--slot-3", "--slot-4", "--slot-5", "--slot-6",
             "--slot-7"),
    "Greek": ("--greek-delta", "--greek-gamma", "--greek-vega", "--greek-theta",
              "--greek-residual"),
}

GROUPS = {**SOLE_CARRIER, **LABELLED}


def _braces() -> list[str]:
    """Every stylesheet closes every block it opens.

    A stray ``}`` is the quietest failure this project has: CSS has no parse error, the
    browser discards from the stray brace to the end of the enclosing block, and the page
    renders with some rules simply not applied. It has now cost two debugging sessions —
    a line of card text rendered at the page's base size for an afternoon — because
    nothing anywhere said anything. Counting braces is crude and catches exactly this.

    Braces inside strings and comments would make the count lie, so both are stripped
    first. No stylesheet here has either, but one will.
    """
    out: list[str] = []
    for css in sorted((ROOT / "app" / "static").rglob("*.css")):
        text = re.sub(r"/\*.*?\*/", "", css.read_text(encoding="utf-8"), flags=re.S)
        text = re.sub(r"\"[^\"\n]*\"|'[^'\n]*'", "", text)
        gap = text.count("{") - text.count("}")
        if gap:
            where = "unclosed" if gap > 0 else "stray closing"
            out.append(f"{css.relative_to(ROOT)}: {abs(gap)} {where} brace"
                       f"{'s' if abs(gap) > 1 else ''}. Everything after it is dropped "
                       f"by the browser without an error")
    return out


def main() -> int:
    if not TOKENS.exists():
        print(f"the token file is missing: {TOKENS.relative_to(ROOT)}")
        return 1
    themes = _blocks(TOKENS.read_text(encoding="utf-8"))
    problems: list[str] = _braces()
    notes: list[str] = []
    checked = 0

    for theme, tokens in themes.items():
        page = resolve(tokens.get("--surface-0", ""), tokens)

        def colour(name: str) -> Rgb | None:
            value = tokens.get(name)
            return resolve(value, tokens, backdrop=page) if value else None

        for text, surfaces in TEXT_ON.items():
            fg = colour(text)
            if fg is None:
                problems.append(f"{theme}: {text} is not defined, or its value cannot be "
                                f"resolved to a colour")
                continue
            for surface in surfaces:
                bg = colour(surface)
                if bg is None:
                    problems.append(f"{theme}: {surface} is not defined, or its value "
                                    f"cannot be resolved to a colour")
                    continue
                ratio = contrast(fg, bg)
                checked += 1
                if ratio < TEXT_RATIO:
                    problems.append(
                        f"{theme}: {text} on {surface} is {ratio:.2f}:1, below the "
                        f"{TEXT_RATIO}:1 the design plan requires for text"
                    )

        for token in DATA_TOKENS:
            fg = colour(token)
            if fg is None:
                problems.append(f"{theme}: {token} is not defined, or its value cannot "
                                f"be resolved to a colour")
                continue
            for surface in DATA_ON:
                bg = colour(surface)
                if bg is None:
                    continue
                ratio = contrast(fg, bg)
                checked += 1
                if ratio < NON_TEXT_RATIO:
                    problems.append(
                        f"{theme}: {token} on {surface} is {ratio:.2f}:1, below the "
                        f"{NON_TEXT_RATIO}:1 a line or marker needs to be seen"
                    )

    # Colour-blind separation. The data colours are theme-independent, so once.
    tokens = themes["dark"]
    for kind in ("protanopia", "deuteranopia"):
        for group, names in GROUPS.items():
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    ca, cb = resolve(tokens[a], tokens), resolve(tokens[b], tokens)
                    if ca is None or cb is None:
                        continue
                    gap = separation(simulate(ca, kind), simulate(cb, kind))
                    if group in SOLE_CARRIER and gap < SOLE_CARRIER_FLOOR:
                        problems.append(
                            f"{group}: {a} and {b} are {gap:.1f} apart under {kind}, "
                            f"below the {SOLE_CARRIER_FLOOR:.0f} a series needs when "
                            f"**colour is all it has**. A line carries no number: two "
                            f"runs would be drawn alike"
                        )
                    elif gap < SEPARATION:
                        notes.append(
                            f"{group}: {a} and {b} are {gap:.1f} apart under {kind} "
                            f"(below {SEPARATION:.0f}); they are told apart by the "
                            f"marker's number or its written label, not by colour"
                        )

    if problems:
        print("the colour check failed:")
        for p in problems:
            print(f"  - {p}")
        print("\nA pair too close to read is a silent failure: the page renders and "
              "nothing raises.")
        return 1
    print(f"the colour check passed ({checked} pairs in two themes)")
    if notes:
        print(f"  {len(notes)} pair(s) are close under colour blindness, which the "
              f"design plan answers with a number on every marker:")
        for n in notes:
            print(f"    · {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
