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
    kind: str  # "equal" | "added" | "removed" | "modified"
    text: str
    # Only set when kind == "modified": the new wording that replaced
    # `text`. Lets the renderer show a single inline word-level diff
    # ("old (new)") instead of two full, separately-duplicated blocks --
    # see word_level_diff() and _reconcile_replace_block().
    replacement: str | None = None


def word_level_diff(old: str, new: str) -> list[tuple[str, str]]:
    """
    Word-level diff between two sentences, as a list of (kind, text)
    runs -- "equal" | "removed" | "added" -- in reading order. Public
    (not `_`-prefixed): the renderer uses this directly to build the
    inline "old (new)" markup for a "modified" DiffSegment, so the
    presentation layer never has to reimplement diff logic, and this
    module stays the single source of truth for what changed.

    Same SequenceMatcher machinery as the rest of this module, just
    applied one level down (words within a sentence, not sentences
    within a document).
    """
    old_words = old.split()
    new_words = new.split()
    matcher = difflib.SequenceMatcher(a=old_words, b=new_words, autojunk=False)
    ops: list[tuple[str, str]] = []
    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag == "equal":
            ops.append(("equal", " ".join(old_words[a_start:a_end])))
        else:
            if tag in ("delete", "replace"):
                ops.append(("removed", " ".join(old_words[a_start:a_end])))
            if tag in ("insert", "replace"):
                ops.append(("added", " ".join(new_words[b_start:b_end])))
    return ops


# How similar a removed sentence and an added sentence must be (word-level
# difflib ratio) within the SAME "replace" opcode to be treated as ONE
# sentence being reworded (rendered as a single inline "old (new)" diff)
# rather than two unrelated, fully-duplicated removed/added blocks.
# Deliberately lower than _NEAR_DUPLICATE_RATIO_THRESHOLD (which means
# "no real change at all") -- this one means "still recognizably the same
# sentence, genuinely edited". Kept well above coincidental overlap from
# shared stopwords ("the", "our", "and"...) between two UNRELATED
# sentences, which real testing put in the 0.2-0.3 range for ordinary
# prose -- 0.4 leaves a safety margin above that noise floor.
_MODIFIED_PAIR_RATIO_THRESHOLD = 0.4


def _reconcile_replace_block(removed_units: list[str], added_units: list[str]) -> list[DiffSegment]:
    """
    Turns ONE "replace" opcode's local removed/added units into segments,
    recognizing two things the naive "all removed, then all added" listing
    doesn't:

    1. Exact duplicates local to this block (a Counter/multiset
       intersection, same technique as `_recover_reordered_matches` but
       scoped to just this block) become "equal".
    2. Remaining removed/added sentences similar enough to plausibly be
       "the same sentence, reworded" (see _MODIFIED_PAIR_RATIO_THRESHOLD)
       are paired into a single "modified" segment instead of two
       full-text removed+added blocks -- real user feedback: reading two
       near-identical paragraphs stacked on top of each other, one struck
       through and one green, took longer and was harder to scan than
       just seeing what actually changed inline. Matched greedily,
       highest similarity first, each sentence used at most once.

    Deliberately scoped to a single opcode's block, not global like the
    two recovery passes above: "this sentence was reworded into that one"
    is a claim about two ADJACENT, contextually-related pieces of text
    (which is exactly what one replace opcode groups together). Matching
    it globally across the whole document would risk pairing two
    unrelated sentences from different parts of a section just because
    they happen to share a few words.
    """
    common = Counter(removed_units) & Counter(added_units)
    remaining_removed_exact = Counter(common)
    remaining_added_exact = Counter(common)

    exact_removed_idxs: set[int] = set()
    for i, unit in enumerate(removed_units):
        if remaining_removed_exact[unit] > 0:
            exact_removed_idxs.add(i)
            remaining_removed_exact[unit] -= 1
    exact_added_idxs: set[int] = set()
    for i, unit in enumerate(added_units):
        if remaining_added_exact[unit] > 0:
            exact_added_idxs.add(i)
            remaining_added_exact[unit] -= 1

    leftover_removed = [(i, u) for i, u in enumerate(removed_units) if i not in exact_removed_idxs]
    leftover_added = [(i, u) for i, u in enumerate(added_units) if i not in exact_added_idxs]

    candidates = []
    for li, (_ri, r) in enumerate(leftover_removed):
        r_words = r.split()
        for lj, (_ai, a) in enumerate(leftover_added):
            ratio = difflib.SequenceMatcher(a=r_words, b=a.split(), autojunk=False).ratio()
            if ratio >= _MODIFIED_PAIR_RATIO_THRESHOLD:
                candidates.append((ratio, li, lj))
    candidates.sort(key=lambda c: c[0], reverse=True)

    # Two tiers above the pairing floor: near-duplicate-strength matches
    # (same bar as _recover_near_duplicate_matches) are unchanged text,
    # not an edit -- "equal", not "modified". Everything else that clears
    # the pairing floor is a real, worth-showing edit.
    paired_as_equal: dict[int, int] = {}
    paired_as_modified: dict[int, int] = {}
    used_added: set[int] = set()
    for ratio, li, lj in candidates:
        if li in paired_as_equal or li in paired_as_modified or lj in used_added:
            continue
        if ratio >= _NEAR_DUPLICATE_RATIO_THRESHOLD:
            paired_as_equal[li] = lj
        else:
            paired_as_modified[li] = lj
        used_added.add(lj)

    segments: list[DiffSegment] = []
    for i, unit in enumerate(removed_units):
        if i in exact_removed_idxs:
            segments.append(DiffSegment(kind="equal", text=unit))
    for li, (_ri, r) in enumerate(leftover_removed):
        if li in paired_as_equal:
            segments.append(DiffSegment(kind="equal", text=r))
        elif li in paired_as_modified:
            segments.append(DiffSegment(kind="modified", text=r, replacement=leftover_added[paired_as_modified[li]][1]))
        else:
            segments.append(DiffSegment(kind="removed", text=r))
    for lj, (_ai, a) in enumerate(leftover_added):
        if lj not in used_added:
            segments.append(DiffSegment(kind="added", text=a))
    return segments


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


# How similar two "removed"/"added" sentences must be (as a difflib ratio
# over their WORDS, not characters -- see _recover_near_duplicate_matches)
# to be treated as unchanged rather than a real edit. 0.95 means at most
# ~5% of the words differ. Raised from an initial 0.99 (~1%) at the user's
# request -- real filings apparently drift by more than a single-word
# encoding artifact between years. Still a deliberate tolerance for
# presentation noise (whitespace/encoding artifacts between two filings,
# HTML entity differences, a stray typo fix), not a licence to ignore
# real content changes -- see the trade-off note below.
_NEAR_DUPLICATE_RATIO_THRESHOLD = 0.95


def _recover_near_duplicate_matches(
    segments: list[DiffSegment], threshold: float = _NEAR_DUPLICATE_RATIO_THRESHOLD
) -> list[DiffSegment]:
    """
    Recovers sentence pairs that are NEARLY identical (>= `threshold`
    word-level similarity) but not byte-identical, so `_recover_reordered_
    matches` (exact match only) didn't catch them.

    Real user feedback on a Netflix report: a sentence showed up as both
    "removed" and "added" even though it read as exactly the same
    sentence. The two filings likely differ in something invisible on the
    page -- a non-breaking space, a smart vs. straight quote, an HTML
    entity that decoded slightly differently between two filing years --
    not in the actual content. Chasing every possible byte-level encoding
    mismatch individually isn't tractable; a similarity tolerance catches
    this whole class of noise at once, which is what was asked for.

    Method: for every remaining removed/added pair, a difflib ratio over
    their WORDS (not characters -- a word-level ratio is a much closer
    proxy for "how many words actually differ" than a character ratio,
    which a single word insertion/deletion can swing disproportionately).
    Pairs are matched greedily, highest ratio first, each sentence used at
    most once, so a removed sentence with two similar-but-different added
    candidates pairs with its closest match, not an arbitrary one.

    Known trade-off, accepted deliberately per this ratio-based approach:
    a small factual change (e.g. one or two figures) inside a long enough
    sentence can push the ratio above threshold and get swallowed as "no
    change" -- the same risk any fixed-percentage tolerance carries. At
    95%, that means roughly 1 changed word per 20 in a sentence can be
    absorbed; the threshold is kept as high as the user's tolerance for
    presentation noise allows, not eliminated.
    """
    removed_idxs = [i for i, seg in enumerate(segments) if seg.kind == "removed"]
    added_idxs = [i for i, seg in enumerate(segments) if seg.kind == "added"]
    if not removed_idxs or not added_idxs:
        return segments

    removed_words = {i: segments[i].text.split() for i in removed_idxs}
    added_words = {i: segments[i].text.split() for i in added_idxs}

    candidates = []
    for ri in removed_idxs:
        r_words = removed_words[ri]
        r_len = len(r_words)
        for ai in added_idxs:
            a_words = added_words[ai]
            a_len = len(a_words)
            # Cheap length-based prefilter before the costlier
            # SequenceMatcher call: two sequences whose lengths already
            # differ by more than the allowed error margin cannot reach
            # `threshold`, whatever their content.
            longer = max(r_len, a_len)
            if longer == 0 or abs(r_len - a_len) > longer * (1 - threshold):
                continue
            ratio = difflib.SequenceMatcher(a=r_words, b=a_words, autojunk=False).ratio()
            if ratio >= threshold:
                candidates.append((ratio, ri, ai))

    if not candidates:
        return segments

    candidates.sort(key=lambda c: c[0], reverse=True)
    matched_removed: set[int] = set()
    matched_added: set[int] = set()
    for _ratio, ri, ai in candidates:
        if ri in matched_removed or ai in matched_added:
            continue
        matched_removed.add(ri)
        matched_added.add(ai)

    result: list[DiffSegment] = []
    for i, seg in enumerate(segments):
        if i in matched_removed:
            continue  # dropped: the matched "added" version below represents it
        if i in matched_added:
            result.append(DiffSegment(kind="equal", text=seg.text))
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
        elif tag == "replace":
            # Has units on BOTH sides -- reconciled locally (exact
            # duplicates -> equal, similar-enough pairs -> a single
            # inline "modified" segment) rather than dumped as two flat
            # removed/added lists. See _reconcile_replace_block's
            # docstring for why this is scoped to the block, not global.
            segments.extend(_reconcile_replace_block(
                prior_units[a_start:a_end], current_units[b_start:b_end]
            ))
        elif tag == "delete":
            # Pure one-sided removal -- no added counterpart in this
            # opcode to pair against.
            for unit in prior_units[a_start:a_end]:
                segments.append(DiffSegment(kind="removed", text=unit))
        elif tag == "insert":
            for unit in current_units[b_start:b_end]:
                segments.append(DiffSegment(kind="added", text=unit))

    # Cross-opcode reordering (see each function's docstring for why this
    # can't be caught opcode-by-opcode) -- both only look at "removed"/
    # "added" segments, so "modified" ones from the block-local
    # reconciliation above are untouched.
    segments = _recover_reordered_matches(segments)
    segments = _recover_near_duplicate_matches(segments)

    added_words = 0
    removed_words = 0
    for seg in segments:
        if seg.kind == "added":
            added_words += _word_count(seg.text)
        elif seg.kind == "removed":
            removed_words += _word_count(seg.text)
        elif seg.kind == "modified":
            # Only the words that actually differ count -- not the whole
            # sentence on each side -- so a report's "N mots changés"
            # reflects the size of the real edit, not sentence length.
            for word_tag, word_text in word_level_diff(seg.text, seg.replacement):
                if word_tag == "removed":
                    removed_words += _word_count(word_text)
                elif word_tag == "added":
                    added_words += _word_count(word_text)

    return TextDiffResult(
        segments=segments,
        similarity_ratio=matcher.ratio(),
        prior_word_count=_word_count(prior_text),
        current_word_count=_word_count(current_text),
        added_word_count=added_words,
        removed_word_count=removed_words,
    )
