"""
Core paragraph-level text diff, built on difflib.SequenceMatcher.

Paragraph granularity (not line or sentence) because
data_layer.text_sections.html_to_text produces blank-line-separated
paragraphs, and a diff at that grain reads like "this paragraph is new /
this paragraph was removed / this paragraph is unchanged" -- what an
advisory reader actually wants to skim, unlike a character- or word-level
diff, which is noisy for prose and hides the shape of what changed.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text)]
    return [p for p in paragraphs if p]


def _word_count(text: str) -> int:
    return len(text.split())


@dataclass(frozen=True)
class DiffSegment:
    kind: str  # "equal" | "added" | "removed"
    text: str


@dataclass(frozen=True)
class TextDiffResult:
    segments: list  # list[DiffSegment], in document order
    similarity_ratio: float  # difflib ratio over paragraphs; 1.0 = identical
    prior_word_count: int
    current_word_count: int
    added_word_count: int
    removed_word_count: int


def diff_text(prior_text: str, current_text: str) -> TextDiffResult:
    """Paragraph-level diff of `prior_text` -> `current_text`."""
    prior_paragraphs = _split_paragraphs(prior_text)
    current_paragraphs = _split_paragraphs(current_text)

    matcher = difflib.SequenceMatcher(
        a=prior_paragraphs, b=current_paragraphs, autojunk=False
    )

    segments: list[DiffSegment] = []
    added_words = 0
    removed_words = 0

    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag == "equal":
            for para in prior_paragraphs[a_start:a_end]:
                segments.append(DiffSegment(kind="equal", text=para))
        else:
            # "delete" and "replace" both remove prior paragraphs;
            # "insert" and "replace" both add current paragraphs.
            if tag in ("delete", "replace"):
                for para in prior_paragraphs[a_start:a_end]:
                    segments.append(DiffSegment(kind="removed", text=para))
                    removed_words += _word_count(para)
            if tag in ("insert", "replace"):
                for para in current_paragraphs[b_start:b_end]:
                    segments.append(DiffSegment(kind="added", text=para))
                    added_words += _word_count(para)

    return TextDiffResult(
        segments=segments,
        similarity_ratio=matcher.ratio(),
        prior_word_count=_word_count(prior_text),
        current_word_count=_word_count(current_text),
        added_word_count=added_words,
        removed_word_count=removed_words,
    )
