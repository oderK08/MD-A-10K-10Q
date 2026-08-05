"""
Groups a diffed section into sub-themes by detected heading lines,
instead of treating it as one long flat sequence of sentence-level
changes.

Why: a real NVIDIA 10-K's Item 1A is internally organized under a
handful of named sub-headings ("Risks Related to Demand, Supply, and
Manufacturing", "Risks Related to Our Global Operating Business", ...).
When the section is substantially rewritten (as NVIDIA's was: 65.7%
similarity), the flat diff renders as many pages of unbroken red-then-
green text with no way to tell which changes cluster around which
underlying topic, or at a glance which sub-theme changed the most.
Splitting the diff by sub-heading -- diffing each sub-theme's content
independently -- makes the same information reviewable by topic instead
of as one undifferentiated wall of text.

Falls back gracefully to a single, unheaded group spanning the whole
text when no heading-shaped lines are found, which is the common case
(a 10-K/10-Q whose Item 1A or Item 7 has no internal sub-headings at
all -- most filers, even most of NVIDIA's own older filings, don't use
this structure). In that case grouped diffing is exactly equivalent to
the flat diff_text() result (one group == the whole text), so there is
no separate "ungrouped" code path to maintain or fall out of sync.

Matching sub-themes between the two filings is NOT purely positional:
a sub-theme can be added, removed, or reordered year over year, so the
two filings' heading sequences are themselves aligned with
difflib.SequenceMatcher (the same tool used for sentence-level diffing
in text_diff.py, just applied one level up, to headings instead of
sentences) rather than assumed to line up 1:1 by position.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from .text_diff import TextDiffResult, diff_text

# A block-level line (its own paragraph in the extracted text, since
# html_to_text turns block-tag closings into newlines) that looks like a
# heading rather than prose: short, no trailing punctuation, not a
# bullet item. Confirmed against a real NVIDIA 10-K, whose Item 1A uses
# exactly this shape for its sub-theme headings -- "Risks Related to
# Demand, Supply, and Manufacturing", not "Risks related to demand,
# supply, and manufacturing." (note: no trailing period, unlike every
# real sentence in the same document).
_MAX_HEADING_WORDS = 12
_BULLET_PREFIXES = ("•", "-", "*")
_TRAILING_PUNCTUATION = ".!?:;,"


def _is_heading_candidate(line: str) -> bool:
    line = line.strip()
    if not line:
        return False
    if line[0] in _BULLET_PREFIXES:
        return False
    if line[-1] in _TRAILING_PUNCTUATION:
        return False
    words = line.split()
    if not (1 <= len(words) <= _MAX_HEADING_WORDS):
        return False
    if not any(c.isalpha() for c in line):
        return False
    return True


def _split_into_groups(text: str) -> list:
    """
    Splits `text` into (heading, content) pairs in document order.
    `heading` is "" for any content appearing before the first detected
    heading, and for the whole text when no heading is found at all.
    """
    lines = [ln for ln in text.split("\n") if ln.strip()]
    heading_positions = [i for i, ln in enumerate(lines) if _is_heading_candidate(ln)]

    if not heading_positions:
        return [("", text)]

    groups: list = []
    if heading_positions[0] > 0:
        preamble = "\n".join(lines[: heading_positions[0]])
        groups.append(("", preamble))

    for idx, pos in enumerate(heading_positions):
        heading = lines[pos].strip()
        end = heading_positions[idx + 1] if idx + 1 < len(heading_positions) else len(lines)
        content = "\n".join(lines[pos + 1 : end])
        groups.append((heading, content))

    return groups


@dataclass(frozen=True)
class DiffGroup:
    heading: str  # "" for the unheaded preamble/whole-text group
    status: str  # "matched" | "added" | "removed" (the sub-theme itself, not its content)
    diff: TextDiffResult


@dataclass(frozen=True)
class GroupedTextDiffResult:
    overall: TextDiffResult  # the exact same flat diff diff_text() would produce -- unchanged aggregate stats
    groups: list  # list[DiffGroup], in document order (prior's order, with insertions placed where they occur)


def diff_text_grouped(prior_text: str, current_text: str) -> GroupedTextDiffResult:
    """
    Computes the ordinary flat diff (`overall`, for aggregate stats:
    similarity ratio, total added/removed word counts -- unchanged
    behavior, used everywhere the flat diff was used before) AND a
    sub-theme breakdown (`groups`) of the same underlying change, for
    rendering. The two are independent views of the same diff, not two
    different diffs -- every word that appears in `overall` also
    appears in exactly one group's diff, and vice versa.
    """
    overall = diff_text(prior_text, current_text)

    prior_groups = _split_into_groups(prior_text)
    current_groups = _split_into_groups(current_text)
    prior_headings = [h for h, _ in prior_groups]
    current_headings = [h for h, _ in current_groups]

    matcher = difflib.SequenceMatcher(a=prior_headings, b=current_headings, autojunk=False)

    groups: list = []
    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(a_end - a_start):
                heading, prior_content = prior_groups[a_start + offset]
                _, current_content = current_groups[b_start + offset]
                groups.append(DiffGroup(
                    heading=heading,
                    status="matched",
                    diff=diff_text(prior_content, current_content),
                ))
        else:
            if tag in ("delete", "replace"):
                for heading, content in prior_groups[a_start:a_end]:
                    groups.append(DiffGroup(
                        heading=heading,
                        status="removed",
                        diff=diff_text(content, ""),
                    ))
            if tag in ("insert", "replace"):
                for heading, content in current_groups[b_start:b_end]:
                    groups.append(DiffGroup(
                        heading=heading,
                        status="added",
                        diff=diff_text("", content),
                    ))

    return GroupedTextDiffResult(overall=overall, groups=groups)


__all__ = ["DiffGroup", "GroupedTextDiffResult", "diff_text_grouped"]
