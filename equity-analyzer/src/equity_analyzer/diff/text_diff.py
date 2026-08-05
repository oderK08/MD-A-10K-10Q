"""
Core text diff, built on difflib.SequenceMatcher, at sentence granularity
within blank-line-delimited blocks.

Why not plain "paragraph" (blank-line-only) splitting, which was the first
version of this module: real SEC filings routinely wrap an entire
multi-sentence block -- sometimes a whole risk factor, sometimes the
entire MD&A -- in a SINGLE `<p>` tag, using bare newlines only to wrap
long sentences for source readability, e.g.:

    <p>Revenue increased 12% year over year, driven by strong demand.
    Gross margin improved by 150 basis points due to favorable costs.
    Operating expenses grew slower than revenue.</p>

`html_to_text` (Module 1) turns each `</p>` into exactly one `\n`, so
blank-line ("\n\n") boundaries only appear BETWEEN such blocks, never
inside one -- confirmed against `tests/fixtures/sample_10k.html`, which
has this exact shape. Splitting purely on blank lines would treat that
whole 3-sentence block as one diff unit: if only the revenue sentence
changes next quarter, the diff would report the ENTIRE block as removed
and re-added, which is exactly the kind of misleading "big change"
signal this tool exists to avoid.

The fix: split on blank lines first (preserving real paragraph/section
boundaries when they exist), then within each resulting block, collapse
bare newlines to spaces (they're mid-sentence line-wrap artifacts, not
paragraph breaks) and split into sentences. Sentence splitting is a
plain regex (period/!/? followed by whitespace and a capital letter or
digit) -- it will occasionally over-split on abbreviations like "U.S."
This is a known, accepted approximation: an over-split sentence still
diffs correctly (it just shows as two "equal" segments instead of one
when unchanged), whereas under-splitting is the failure mode that
produces false "big change" signals, which is the one this module is
built to avoid.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass

_BLOCK_SPLIT_RE = re.compile(r"\n\s*\n+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")


def _split_paragraphs(text: str) -> list[str]:
    """
    Splits `text` into diffable units: blank lines mark real block
    boundaries; within a block, line-wrap newlines are collapsed and the
    result is split into sentences. See module docstring for why.
    """
    blocks = [b.strip() for b in _BLOCK_SPLIT_RE.split(text) if b.strip()]
    units: list[str] = []
    for block in blocks:
        flat = re.sub(r"\s*\n\s*", " ", block)
        flat = re.sub(r"[ \t]+", " ", flat).strip()
        if not flat:
            continue
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(flat) if s.strip()]
        units.extend(sentences or [flat])
    return units


def _word_count(text: str) -> int:
    return len(text.split())


@dataclass(frozen=True)
class DiffSegment:
    kind: str  # "equal" | "added" | "removed"
    text: str


def _recover_reordered_matches(segments: list[DiffSegment]) -> list[DiffSegment]:
    """
    Recovers sentences that are byte-identical but merely REORDERED.

    `SequenceMatcher.get_opcodes()` is LCS-based: it only reports two units
    as "equal" when their relative order is preserved on both sides. A
    filing that reshuffles the same risk-factor sentences into a new order
    (common when a company reorganizes a section without changing its
    content) defeats that -- every reordered sentence comes back as a
    spurious removed+added pair, even though nothing actually changed.
    This isn't confined to a single "replace" opcode either: e.g. swapping
    the order of just two sentences produces an ("insert", ...), ("equal",
    ...), ("delete", ...) sequence -- the reordered pair straddles an
    unrelated "equal" opcode in between.

    Fix: applied globally across the WHOLE already-built segment list (not
    opcode-by-opcode), a Counter (multiset) intersection between all
    "removed" and all "added" texts finds content that appears on both
    sides regardless of position. For each matched occurrence, the
    "removed" one is reclassified as "equal" and its paired "added"
    counterpart is dropped outright (it's the same sentence -- reporting
    it a second time would double-count it). Only genuine leftover text
    (present on one side but with no available match on the other, even
    after accounting for duplicates) stays removed/added.
    """
    removed_counts = Counter(seg.text for seg in segments if seg.kind == "removed")
    added_counts = Counter(seg.text for seg in segments if seg.kind == "added")
    common = removed_counts & added_counts
    if not common:
        return segments

    convert_remaining = Counter(common)
    drop_remaining = Counter(common)
    result: list[DiffSegment] = []
    for seg in segments:
        if seg.kind == "removed" and convert_remaining[seg.text] > 0:
            result.append(DiffSegment(kind="equal", text=seg.text))
            convert_remaining[seg.text] -= 1
        elif seg.kind == "added" and drop_remaining[seg.text] > 0:
            drop_remaining[seg.text] -= 1
            # Dropped, not appended: already represented by the "equal"
            # segment its matched "removed" counterpart became above.
        else:
            result.append(seg)
    return result


@dataclass(frozen=True)
class TextDiffResult:
    segments: list  # list[DiffSegment], in document order
    similarity_ratio: float  # difflib ratio over diff units; 1.0 = identical
    prior_word_count: int
    current_word_count: int
    added_word_count: int
    removed_word_count: int


def diff_text(prior_text: str, current_text: str) -> TextDiffResult:
    """Sentence-level diff of `prior_text` -> `current_text` (see module docstring)."""
    prior_units = _split_paragraphs(prior_text)
    current_units = _split_paragraphs(current_text)

    matcher = difflib.SequenceMatcher(
        a=prior_units, b=current_units, autojunk=False
    )

    segments: list[DiffSegment] = []

    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag == "equal":
            for unit in prior_units[a_start:a_end]:
                segments.append(DiffSegment(kind="equal", text=unit))
        else:
            # "delete" and "replace" both remove prior units;
            # "insert" and "replace" both add current units. Any
            # reordered-but-identical text hiding across these is
            # recovered afterwards, globally, by
            # _recover_reordered_matches -- see its docstring for why
            # that has to happen after the fact rather than per-opcode.
            if tag in ("delete", "replace"):
                for unit in prior_units[a_start:a_end]:
                    segments.append(DiffSegment(kind="removed", text=unit))
            if tag in ("insert", "replace"):
                for unit in current_units[b_start:b_end]:
                    segments.append(DiffSegment(kind="added", text=unit))

    segments = _recover_reordered_matches(segments)
    added_words = sum(_word_count(seg.text) for seg in segments if seg.kind == "added")
    removed_words = sum(_word_count(seg.text) for seg in segments if seg.kind == "removed")

    return TextDiffResult(
        segments=segments,
        similarity_ratio=matcher.ratio(),
        prior_word_count=_word_count(prior_text),
        current_word_count=_word_count(current_text),
        added_word_count=added_words,
        removed_word_count=removed_words,
    )
