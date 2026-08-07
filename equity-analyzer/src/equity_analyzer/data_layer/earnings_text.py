"""
Turning an earnings press release or call transcript into text worth
diffing.

These documents share nothing structurally with a 10-K. There are no
"Item 1A" anchors to find, so none of `text_sections.py` applies; what
there IS instead:

  A PRESS RELEASE is a short narrative (results, commentary, a CEO
  quote, and the outlook) followed by the financial statements as HTML
  tables. The narrative is the news. The tables are the same numbers
  already available, better, from XBRL -- and flattened to text they
  become unreadable runs of digits that diff as changed every single
  quarter, which is both true and useless. They are dropped, and the
  fact that they were dropped is recorded rather than hidden.

  A TRANSCRIPT is speaker turns. The turn headers ("Operator", "Jane Doe
  -- Chief Executive Officer") are what a reader navigates by, and they
  survive as heading-shaped lines that the existing sub-theme grouping
  already understands.

THE OUTLOOK SECTION IS SINGLED OUT. Everything else in a press release
restates the quarter that just ended, which the market has already
priced by the time anyone reads it. Guidance is the part that is genuinely
new information, it is the part that moves the stock, and quarter over
quarter it is the most comparable text in the whole document: filers
write it to a template and change the numbers. That makes it the single
highest-signal thing this module can hand downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .text_sections import html_to_text

# A flattened financial-statement row: a label followed by columns of
# figures ("Cost of goods sold (151,200) (140,110) 7.9%"). `html_to_text`
# turns each </tr> into a newline, so one row is one line.
#
# Diffing these adds a changed segment for every line of every statement,
# every quarter, drowning the narrative that this module exists to
# surface -- and the same numbers are already in the report, sourced from
# XBRL, where they carry a period and a concept name.
#
# The discriminator is NOT the share of alphabetic characters: a
# long-labelled row ("Cost of goods sold") is over half letters and
# passed a ratio test, while a terse one did not. What actually separates
# them is that a row has SEVERAL numeric columns, few words, and no
# sentence punctuation at the end, whereas a sentence quoting figures
# ("revenue of $217.6 million, up 12% ...") is long and ends in a period.
_NUMERIC_COLUMN_RE = re.compile(r"^[($]?-?[\d,.]+[)%]*$|^[($]?-?[\d,.]+\s*%$")
_WORD_RE = re.compile(r"[A-Za-z]{2,}")

_MIN_TABLE_ROW_CHARS = 24
_MIN_NUMERIC_COLUMNS = 2
# A line with at least this many real words is prose even without a
# closing period, which is what a paragraph wrapped across block tags
# looks like.
_PROSE_WORD_COUNT = 8
_SENTENCE_ENDINGS = (".", "!", "?", ":", ";", '."', '.”')

_OUTLOOK_HEADING_RE = re.compile(
    r"^\W*(?:business\s+)?(?:outlook|guidance|financial\s+outlook|"
    r"(?:first|second|third|fourth)\s+quarter\s+(?:\w+\s+)?(?:outlook|guidance))\b",
    re.IGNORECASE,
)
# A heading marks where the outlook ENDS as well: the section runs until
# the next heading-shaped line, and these are the ones that conventionally
# follow it.
_AFTER_OUTLOOK_RE = re.compile(
    r"^\W*(?:conference\s+call|webcast|about\s+\w|forward[\s-]looking|"
    r"safe\s+harbor|use\s+of\s+non[\s-]gaap|investor\s+(?:relations|contact)|"
    r"media\s+contact|contacts?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EarningsDocumentText:
    """
    `narrative` is the prose, tables removed. `outlook` is the guidance
    section on its own when one was found, and is ALSO still inside
    `narrative` -- it is a spotlight, not a slice, so nothing downstream
    has to reassemble the document to see it in context.
    """
    narrative: str
    outlook: Optional[str]
    dropped_table_lines: int

    @property
    def word_count(self) -> int:
        return len(self.narrative.split())


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < _MIN_TABLE_ROW_CHARS:
        return False
    if stripped.endswith(_SENTENCE_ENDINGS):
        return False
    if len(_WORD_RE.findall(stripped)) >= _PROSE_WORD_COUNT:
        return False
    numeric_columns = sum(1 for token in stripped.split() if _NUMERIC_COLUMN_RE.match(token))
    return numeric_columns >= _MIN_NUMERIC_COLUMNS


def _slice_outlook(lines: list) -> Optional[str]:
    """
    The guidance section: from an outlook heading to the next heading
    that conventionally follows it (or the end of the document).

    Bounded by a KNOWN set of following headings rather than by "the next
    short line", because a press release's outlook is itself full of
    short lines (one per metric guided). Stopping at the first of those
    would return a one-line section and quietly lose the guidance.
    """
    start = None
    for index, line in enumerate(lines):
        if _OUTLOOK_HEADING_RE.match(line.strip()):
            start = index
            break
    if start is None:
        return None

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _AFTER_OUTLOOK_RE.match(lines[index].strip()):
            end = index
            break

    body = [line for line in lines[start + 1 : end] if line.strip()]
    return "\n".join(body).strip() or None


def extract_earnings_text(html: str) -> EarningsDocumentText:
    """
    Plain text of a press release or transcript exhibit, tables removed
    and the outlook section located.

    Reuses `html_to_text`, so everything already learned from real
    filings applies here unchanged: numeric entities decoded, words
    rejoined across printer tags, page furniture stripped. Those quirks
    come from the same financial printers that typeset the 10-K, so an
    8-K exhibit has them too.
    """
    text = html_to_text(html)
    lines = text.split("\n")

    kept = []
    dropped = 0
    for line in lines:
        if _is_table_row(line):
            dropped += 1
            continue
        kept.append(line)

    narrative = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return EarningsDocumentText(
        narrative=narrative,
        outlook=_slice_outlook(kept),
        dropped_table_lines=dropped,
    )


__all__ = ["EarningsDocumentText", "extract_earnings_text"]
