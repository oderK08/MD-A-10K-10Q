"""
Extracts the sections we care about (Risk Factors, MD&A, Controls) from the
raw HTML of a 10-K or 10-Q.

Seven rigor problems solved here, all real and all silent-failure-prone
if ignored -- all but the first two were only discovered by running
against real filings (Microsoft, Coca-Cola, NVIDIA) via the project's
GitHub Actions reliability run, not against the hand-written test
fixtures:

1. TABLE OF CONTENTS FALSE POSITIVES: every 10-K/10-Q has a table of
   contents near the top that also contains the literal text "Item 1A."
   etc. as a hyperlink. A naive "find the first occurrence of Item 1A"
   grabs the TOC entry (a few words) instead of the real section (pages
   of text). We find ALL occurrences of each item header and pick the
   one followed by the most content before the next header -- the TOC
   entry is always immediately followed by the next TOC entry, so it
   loses this comparison.

2. 10-Q BOILERPLATE: Part II Item 1A of a 10-Q is very often just
   "there have been no material changes to the risk factors disclosed
   in our Annual Report on Form 10-K". Treating that as "no textual
   change vs last quarter" would be correct, but treating it the same
   as a genuinely re-written risk factors section (which DOES carry
   signal) is wrong. We flag it explicitly via
   `is_risk_factors_boilerplate` so the diff module can skip it rather
   than reporting a misleading "0% change" data point.

3. WORDS SPLIT ACROSS TAGS: real filings from financial printers wrap
   fragments of a single word in their own inline tag for exact layout
   (found verbatim in a Microsoft 10-K: "RIS<span>K</span> FACTORS").
   Blindly turning every tag into a space broke "RISK" into "RIS" + "K",
   silently defeating the header regex. `_replace_tag` only collapses a
   tag to nothing when it sits directly between two alphanumeric
   characters (a genuine mid-word split); every other tag still becomes
   a space, so adjacent-but-separate elements (e.g. table cells) don't
   get glued together.

4. UNDECODED NUMERIC HTML ENTITIES: a Coca-Cola 10-K uses the numeric
   entity "&#160;" (not the named "&nbsp;") between "Item 7." and
   "Management's Discussion". A literal, undecoded "&#160;" is not
   whitespace to a regex, so the section silently failed to extract. We
   use the standard library's `html.unescape`, which decodes every named
   AND numeric entity, instead of a hand-rolled list that only covered a
   handful of named ones.

5. SENTENCE-INTERNAL CROSS-REFERENCES TO OTHER ITEMS: a real NVIDIA
   10-K's MD&A opens with almost universal boilerplate -- "...should be
   read in conjunction with 'Item 1A. Risk Factors,' our Consolidated
   Financial Statements..." -- a reference to another item sitting
   mid-sentence. `ANY_ITEM_HEADER`, which only checks the shape "item
   <n>[letter]. <words>", can't tell that apart from a real heading, so
   it used to end the section right there: NVIDIA's real ~40,000-word
   MD&A came back as 27 words. `_find_next_real_header` only accepts a
   header-shaped match as a section boundary when it's preceded by a
   newline (i.e. sits on its own line, like a real block-level heading)
   rather than mid-paragraph like a cross-reference.

6. PAGE FURNITURE INSIDE SENTENCES: a lone page number and a repeated
   "Table of Contents" jump-link marker, inserted by the financial
   printer at every page boundary, land INSIDE the extracted text with
   no other whitespace cue -- verbatim from a real NVIDIA 10-K:
   "...cause our stock \n 13 \n\n Table of Contents \n\n price to
   decline." Left in, this pollutes diff and sentiment scoring with
   spurious tokens, and the blank line it introduces fools the diff
   module's paragraph splitter into treating one real sentence as two.
   `_PAGE_FURNITURE_RUN_RE` drops these runs (and the newlines around
   them) before anything downstream sees the text.

7. ITEM NUMBER AND TITLE ON SEPARATE LINES: found on a real Microsoft
   10-K run that extracted Item 1A as TWENTY-TWO words. Two causes, both
   from allowing `\s` (which matches newlines) between an item number
   and its title:
   - a table-of-contents ROW puts the two in separate cells, rendered as
     separate lines -- "Item 1A. \n\n\n\n Risk Factors Item 1B." --
     so the TOC row matched the heading pattern and, being earlier in
     the document, won. Note this is a DIFFERENT failure from point 1:
     the most-content-wins tie-break there only helps when the real
     heading also matches, and here it did not (see below);
   - the printer repeats a bare "PART I \n Item 1A" running header at
     every page break INSIDE the section, and with the newline allowed
     that bare marker absorbed the following prose as its "title",
     reading as a real section boundary.
   Both are fixed by requiring the title on the SAME line as the number
   (`_H`, horizontal whitespace only).
   Compounding it, the real heading read "ITEM 1A. RIS K FACTORS": the
   same split-word problem as point 3, but surviving as a real space
   rather than being repaired -- so the only thing left matching was the
   TOC. `_split_tolerant` lets each keyword absorb one stray space.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape as _html_unescape

from .models import FilingTextSections

# HORIZONTAL whitespace only -- deliberately NOT `\s`, which matches
# newlines. A real item heading has its number and its title on the SAME
# line ("ITEM 1A. RISK FACTORS"); a table-of-contents row has them in
# separate cells, which `html_to_text` renders as separate lines. Using
# `\s*` between the two made a TOC row indistinguishable from a heading,
# and that is exactly how a real Microsoft 10-K run anchored Item 1A on
# the table of contents and extracted 22 words instead of the section
# (verbatim from that run's debug dump: "Item 1A. \n\n\n\n Risk Factors
# Item 1B."). See the module docstring, point 7.
_H = r"[ \t\xa0]"
# The same set as the BODY of a character class, for embedding inside a
# larger one. Interpolating `_H` there instead would nest `[...]` in
# `[...]`, whose first `]` closes the class early and silently changes
# what the pattern means -- which is exactly what happened while writing
# this fix, and is why the two forms are now named separately.
_HC = r" \t\xa0"


def _split_tolerant(word: str) -> str:
    """
    A regex matching `word` even when the financial printer has split it
    with a stray space.

    Real Microsoft 10-K, verbatim from the extracted plaintext: "ITEM
    1A. RIS K FACTORS" -- the printer put markup inside the word, which
    survives tag-stripping as a space (or arrives as a `&#160;` that
    decodes to NBSP). A plain `risk` never matches that, so the real
    heading was invisible to the extractor and only the TOC row matched.

    Allows at most ONE horizontal space between consecutive letters,
    which covers the observed one-split-per-word case without letting
    the pattern wander across unrelated prose.
    """
    return (_H + r"?").join(re.escape(c) for c in word)


def _heading(number: str, *title_words: str) -> str:
    """
    Builds an item-heading pattern: the item number and its title, on the
    same line, tolerant of printer-injected splits inside each word.
    """
    title = (_H + r"+").join(_split_tolerant(w) for w in title_words)
    return rf"item{_H}+{number}\.?{_H}*[-–—:]?{_H}*{title}"


# Item headers we look for. Order matters: we search 10-K style first,
# fall back to 10-Q style (Part I/II numbering differs).
ITEM_PATTERNS = {
    "item_1a_risk_factors": [
        _heading("1a", "risk", "factors"),
    ],
    "item_7_mdna": [
        _heading("7", "management") + rf".?s{_H}+" + (_H + r"+").join(
            _split_tolerant(w) for w in ("discussion", "and", "analysis")
        ),
        _heading("2", "management") + rf".?s{_H}+" + (_H + r"+").join(
            _split_tolerant(w) for w in ("discussion", "and", "analysis")
        ),
    ],
    "item_9a_controls": [
        _heading("9a", "controls", "and", "procedures"),
        _heading("4", "controls", "and", "procedures"),
    ],
}

# Any item header at all, used as the "next section" boundary when
# extracting the content that follows a chosen header.
#
# The title must sit on the SAME line as the item number, for the same
# reason as above plus a second one found on the same Microsoft run: the
# printer repeats a bare "PART I \n Item 1A" running header at every page
# break inside the section. With `\s` allowing the newline, that bare
# marker swallowed the following prose as its "title" and read as a real
# section boundary, truncating the section at the first page break.
ANY_ITEM_HEADER = re.compile(
    rf"item{_H}+\d+[a-c]?\.?{_H}*[-–—:]?{_H}*[a-z][a-z{_HC},'&]{{2,80}}",
    re.IGNORECASE,
)

def _find_next_real_header(text: str, start: int) -> re.Match | None:
    """
    Finds the next occurrence of ANY_ITEM_HEADER after `start` that looks
    like an actual section heading, not a sentence-internal cross-
    reference to another item.

    Confirmed against a real NVIDIA 10-K: its MD&A opens with the almost
    universal boilerplate "...should be read in conjunction with 'Item
    1A. Risk Factors,' our Consolidated Financial Statements..." -- a
    reference to Item 1A sitting mid-sentence, which ANY_ITEM_HEADER
    (which only checks the shape "item <n>[letter]. <words>") cannot
    tell apart from a real heading. Left unfiltered, this cut the
    extracted MD&A off after 27 words instead of the real ~40,000+.
    Because this exact phrasing opens the MD&A of nearly every 10-K, this
    almost certainly isn't NVIDIA-specific.

    The distinguishing signal: `html_to_text` turns block-level tag
    closings (</p>, </div>, </tr>, </li>, <br>) into newlines, so a real
    heading -- a block element on its own -- is preceded by a newline
    (ignoring spaces/tabs); a cross-reference sits inside a paragraph's
    running prose, preceded by ordinary sentence text.
    """
    pos = start
    while True:
        match = ANY_ITEM_HEADER.search(text, pos=pos)
        if match is None:
            return None
        before = text[:match.start()].rstrip(" \t")
        if not before or before.endswith("\n"):
            return match
        pos = match.end()


BOILERPLATE_PATTERNS = [
    r"no\s+material\s+change",
    r"not\s+been\s+any\s+material\s+change",
    r"there\s+have\s+been\s+no\s+material\s+changes",
]

_TAG_RE = re.compile(r"<[^>]+>")
# Includes \xa0 (NBSP) -- entity decoding turns "&#160;"/"&nbsp;" into a
# real NBSP character. Python's \s already matches NBSP for header
# regexes, so this isn't needed for extraction to work, but leaving raw
# \xa0 in the output text is confusing to read and to compare in tests.
_WHITESPACE_RE = re.compile(r"[ \t\xa0]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

# A financial printer inserts a lone page number and a repeated "Table
# of Contents" jump-link marker at every page boundary -- found verbatim
# in a real NVIDIA 10-K, sitting INSIDE a sentence with no other
# whitespace cue: "...cause our stock \n 13 \n\n Table of Contents \n\n
# price to decline." Left in, each occurrence becomes its own spurious
# diff/sentiment token, AND the blank line it introduces (the "\n\n"
# before/after "Table of Contents") fools the diff module's paragraph
# splitter into treating one real sentence as two separate blocks.
# Matched and dropped, together with the newlines around it, before
# anything downstream sees the text.
_PAGE_FURNITURE_RUN_RE = re.compile(
    r"\n\s*(?:(?:\d{1,4}|table of contents)\s*\n\s*)+",
    re.IGNORECASE,
)


def _replace_tag(match: re.Match) -> str:
    """
    Replaces one matched tag with either nothing or a single space,
    depending on what's immediately outside it in the ORIGINAL html.

    Real SEC filings (typeset by financial printers for exact layout)
    routinely wrap a fragment of a single word in its own tag, e.g.
    "RIS<span class="Apple-converted-space">K</span> FACTORS" -- found
    verbatim in a real Microsoft 10-K, where it broke "RISK" into "RIS"
    and "K" once the tag became a space, so "risk\\s+factors" no longer
    matched anything.

    We can't just drop the space unconditionally though: that would glue
    together content from adjacent, genuinely separate elements, e.g.
    "<td>Revenue</td><td>1000</td>" must still become "Revenue 1000", not
    "Revenue1000". The distinguishing signal is whether the tag sits
    directly between two alphanumeric characters with nothing else (no
    other tag boundary, no existing whitespace) between them -- that
    only happens for a genuine mid-word split, never for a boundary
    between two elements (which always has a tag-closing/opening
    character like ">" or "<" immediately adjacent instead).
    """
    text = match.string
    start, end = match.span()
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before.isalnum() and after.isalnum():
        return ""
    return " "


def html_to_text(html: str) -> str:
    """Strips HTML tags to plain text while keeping paragraph breaks."""
    # Turn common block-level closings into newlines before stripping tags,
    # so paragraphs don't get smashed together.
    html = re.sub(r"(?i)</(p|div|tr|br|li)\s*>", "\n", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = _TAG_RE.sub(_replace_tag, html)
    # Decode HTML entities (named AND numeric, e.g. both "&nbsp;" and its
    # numeric form "&#160;") before collapsing whitespace -- an
    # undecoded "&#160;" is not whitespace to a regex, and a real Coca-
    # Cola 10-K uses exactly that numeric form between "Item 7." and
    # "Management's Discussion", which silently defeated the section
    # header match when only a handful of named entities were decoded.
    text = _html_unescape(text)
    text = _WHITESPACE_RE.sub(" ", text)
    # Collapse page furniture (page numbers, repeated "Table of
    # Contents") to a single space BEFORE the multi-newline collapse
    # below, since a removed furniture run can itself leave behind a
    # stray "\n\n" that would otherwise be mistaken for a real paragraph
    # break by the multi-newline collapse.
    text = _PAGE_FURNITURE_RUN_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def _find_best_header_match(text: str, patterns: list[str]) -> re.Match | None:
    """
    Finds all occurrences of any pattern in `patterns`, and returns the
    one followed by the most content before the next item-header-like
    string -- this is what distinguishes a real section start from a
    table-of-contents entry.
    """
    candidates: list[re.Match] = []
    for pattern in patterns:
        candidates.extend(re.finditer(pattern, text, re.IGNORECASE))
    if not candidates:
        return None

    def content_length_after(match: re.Match) -> int:
        start = match.end()
        # Search from immediately after this header's own text. A TOC
        # entry is followed almost immediately by the next TOC entry
        # (a short gap); a real section is followed by substantial prose
        # before the next header, or by the end of the document.
        next_header = _find_next_real_header(text, start)
        end = next_header.start() if next_header else len(text)
        return end - start

    return max(candidates, key=content_length_after)


def _extract_section_text(text: str, match: re.Match) -> str:
    start = match.end()
    next_header = _find_next_real_header(text, start)
    end = next_header.start() if next_header else len(text)
    return text[start:end].strip()


def _is_boilerplate(section_text: str) -> bool:
    if len(section_text) > 600:
        # A genuinely rewritten section is long; boilerplate disclaimers
        # are a sentence or two. This length gate avoids false positives
        # where a long section merely happens to mention "no material
        # change" about one specific risk among many.
        return False
    return any(
        re.search(p, section_text, re.IGNORECASE) for p in BOILERPLATE_PATTERNS
    )


def extract_sections(html: str) -> FilingTextSections:
    text = html_to_text(html)

    result = FilingTextSections()

    match_1a = _find_best_header_match(text, ITEM_PATTERNS["item_1a_risk_factors"])
    if match_1a:
        section = _extract_section_text(text, match_1a)
        result.item_1a_risk_factors = section
        result.is_risk_factors_boilerplate = _is_boilerplate(section)

    match_7 = _find_best_header_match(text, ITEM_PATTERNS["item_7_mdna"])
    if match_7:
        result.item_7_mdna = _extract_section_text(text, match_7)

    match_9a = _find_best_header_match(text, ITEM_PATTERNS["item_9a_controls"])
    if match_9a:
        result.item_9a_controls = _extract_section_text(text, match_9a)

    return result
