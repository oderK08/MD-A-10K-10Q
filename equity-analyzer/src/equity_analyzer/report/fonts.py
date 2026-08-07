"""
Resolves the report's body font to a real font FILE on this machine, so
xhtml2pdf can embed it in the generated PDF.

Why a file and not just a CSS family name: xhtml2pdf does not consult
the system font database. A bare `font-family: Lato` silently falls back
to Helvetica in the PDF (confirmed with `pdffonts` on a real render,
which showed only the built-in Helvetica). Only an `@font-face` rule
pointing at an actual `.ttf` path gets the font embedded -- verified the
same way, `pdffonts` then reports the subsetted family with `emb yes`.

Why Lato rather than the requested Seravek: Seravek is a proprietary
Apple font, shipped with macOS only. It is not present on the GitHub
Actions runners that generate these reports, and its licence does not
permit bundling it in this repository. Lato was chosen (explicitly, by
the user, over keeping Seravek as a Mac-only CSS fallback) as the
closest freely-redistributable humanist sans: same warm, slightly
editorial feel, and available as a plain apt package (`fonts-lato`) so
CI and a developer machine render identically.

Degrades honestly: when no font file is found, `font_face_css()` returns
"" and the stylesheet's own generic `sans-serif` stack applies. The
report still renders -- just in the default face -- rather than failing
or silently pretending the font was applied.
"""

from __future__ import annotations

from pathlib import Path

# Family name used in the stylesheet. Must match the @font-face rule.
BODY_FONT_FAMILY = "ReportSans"

# Searched in order; first hit wins. Covers the Debian/Ubuntu apt layout
# (both this sandbox and the ubuntu-latest CI runner), plus a
# repo-local vendored copy for anyone who'd rather not install a system
# package.
_CANDIDATE_DIRS = [
    Path("/usr/share/fonts/truetype/lato"),
    Path("/usr/share/fonts/lato"),
    Path(__file__).resolve().parent / "assets" / "fonts",
]

# (css weight, css style, filename). Regular and bold are what the
# report actually uses; italic is used by the muted/disclaimer notes.
_FACES = [
    ("normal", "normal", "Lato-Regular.ttf"),
    ("bold", "normal", "Lato-Bold.ttf"),
    ("normal", "italic", "Lato-Italic.ttf"),
    ("bold", "italic", "Lato-BoldItalic.ttf"),
]


def _find(filename: str):
    for directory in _CANDIDATE_DIRS:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def font_face_css() -> str:
    """
    `@font-face` rules for the report font, or "" when the font isn't
    installed on this machine.

    Returns "" (rather than partial rules) unless the REGULAR face is
    found: a stylesheet declaring the family with only a bold face would
    render all body text bold, which is worse than falling back cleanly
    to the generic sans-serif stack.
    """
    regular = _find("Lato-Regular.ttf")
    if regular is None:
        return ""

    rules = []
    for weight, style, filename in _FACES:
        path = _find(filename)
        if path is None:
            continue
        rules.append(
            f"@font-face {{ font-family: {BODY_FONT_FAMILY};"
            f" src: url({path});"
            f" font-weight: {weight}; font-style: {style}; }}"
        )
    return "\n  ".join(rules)


def body_font_stack() -> str:
    """
    The `font-family` value for body text. Always ends in a generic
    fallback so text stays readable when the font file is missing.
    """
    return f"{BODY_FONT_FAMILY}, 'Lato', 'Seravek', sans-serif"
