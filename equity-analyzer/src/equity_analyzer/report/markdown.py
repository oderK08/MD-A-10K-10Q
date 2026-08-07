"""
The model answers in markdown; the report is HTML. This converts one to
the other, and nothing more.

DELIBERATELY NOT A MARKDOWN LIBRARY. The input is not arbitrary markdown
from the internet, it is the answer to a prompt that specifies its own
structure: five level-two headings, bullets, occasional bold, quoted
sentences. Supporting tables, images, footnotes and nested lists would
be code with no caller. What a general library WOULD add is its output
assumptions, and this document renders through xhtml2pdf, whose CSS
support is narrow enough (no flexbox, no grid) that the report's
stylesheet is written to it specifically.

EVERYTHING IS ESCAPED BEFORE ANY MARKUP IS ADDED. The text passing
through here is a quotation of an earnings call, which routinely
contains "&" and "<" in the ordinary course of business ("R&D",
"<10%"). Escaping after inserting tags would eat the tags; escaping
never would corrupt the page.
"""

from __future__ import annotations

import re
from html import escape as _e

# Bullets, in every form a model reasonably emits.
_BULLET_RE = re.compile(r"^\s*(?:[-*•‣◦]|\d+[.)])\s+(.*)$")
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.*?)\s*#*\s*$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    """Escapes, then restores the one inline mark the prompt asks for."""
    return _BOLD_RE.sub(r"<strong>\1</strong>", _e(text))


def truncate_words(text: str, max_words: int) -> tuple:
    """
    Returns (text, was_truncated), cut at the last sentence end that fits.

    Cutting mid-sentence is worse than it sounds here: the sentences
    being cut are quotations of what management said, and half a
    quotation attributed as a whole one is a misquote. The sentence
    boundary is only honoured if it falls in the second half of the
    remaining budget, so a passage with no full stops loses its tail
    rather than almost all of itself.

    LINE BREAKS SURVIVE THE CUT, and that is the whole reason this walks
    the text line by line instead of `" ".join(text.split()[:n])`. The
    obvious one-liner silently joins every line into one, which for
    markdown means the "##" opening the first heading swallows the
    entire answer: the page then renders as one enormous heading rather
    than as five sections. Measured, not theorised, and it cost a page.
    """
    if len(text.split()) <= max_words:
        return text, False

    kept = []
    remaining = max_words
    for line in text.split("\n"):
        words = line.split()
        if not words:
            kept.append("")
            continue
        if len(words) <= remaining:
            kept.append(line)
            remaining -= len(words)
            continue
        partial = " ".join(words[:remaining])
        last_stop = partial.rfind(".")
        if last_stop > len(partial) // 2:
            partial = partial[: last_stop + 1]
        if partial:
            kept.append(partial)
        break

    return "\n".join(kept).rstrip(), True


def markdown_to_html(text: str, *, heading_tag: str = "h2") -> str:
    """
    Converts the model's answer to the report's HTML.

    `heading_tag` exists because the same text is a page's worth of
    content here but could be a subsection elsewhere, and a document
    whose heading levels do not nest reads wrong even when it looks
    right.
    """
    out = []
    bullets = []

    def flush_bullets():
        # A <ul> would be the obvious markup and is what a first version
        # used. In the rendered PDF its marker came out as a missing
        # glyph: xhtml2pdf draws the default bullet from a character the
        # embedded report font does not carry, and the report embeds its
        # own font on purpose. Found by reading a real PDF, not by
        # reading the HTML, which is the only way this class of bug ever
        # shows up. The middle dot below is plain Latin-1 and present in
        # every font this report can fall back to; the negative
        # text-indent gives the hanging indent a list would have.
        if bullets:
            out.extend(f'<p class="bullet">· {item}</p>' for item in bullets)
            bullets.clear()

    for raw_line in (text or "").split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            flush_bullets()
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_bullets()
            out.append(f"<{heading_tag}>{_inline(heading.group(2))}</{heading_tag}>")
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            bullets.append(_inline(bullet.group(1)))
            continue

        flush_bullets()
        out.append(f"<p>{_inline(line.strip())}</p>")

    flush_bullets()
    return "\n".join(out)


__all__ = ["markdown_to_html", "truncate_words"]
