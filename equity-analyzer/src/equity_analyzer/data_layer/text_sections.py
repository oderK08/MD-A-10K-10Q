"""
Extracts the sections we care about (Risk Factors, MD&A, Controls) from the
raw HTML of a 10-K or 10-Q.

Four rigor problems solved here, all real and all silent-failure-prone
if ignored -- the last two were only discovered by running against real
filings (a Microsoft and a Coca-Cola 10-K) via the project's GitHub
Actions reliability run, not against the hand-written test fixtures:

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
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape as _html_unescape

from .models import FilingTextSections

# Item headers we look for. Order matters: we search 10-K style first,
# fall back to 10-Q style (Part I/II numbering differs).
ITEM_PATTERNS = {
    "item_1a_risk_factors": [
        r"item\s+1a\.?\s*[-–—:]?\s*risk\s+factors",
    ],
    "item_7_mdna": [
        r"item\s+7\.?\s*[-–—:]?\s*management.?s\s+discussion\s+and\s+analysis",
        r"item\s+2\.?\s*[-–—:]?\s*management.?s\s+discussion\s+and\s+analysis",
    ],
    "item_9a_controls": [
        r"item\s+9a\.?\s*[-–—:]?\s*controls\s+and\s+procedures",
        r"item\s+4\.?\s*[-–—:]?\s*controls\s+and\s+procedures",
    ],
}

# Any item header at all, used as the "next section" boundary when
# extracting the content that follows a chosen header.
ANY_ITEM_HEADER = re.compile(
    r"item\s+\d+[a-c]?\.?\s*[-–—:]?\s*[a-z][a-z\s,'&]{2,80}",
    re.IGNORECASE,
)

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
        next_header = ANY_ITEM_HEADER.search(text, pos=start)
        end = next_header.start() if next_header else len(text)
        return end - start

    return max(candidates, key=content_length_after)


def _extract_section_text(text: str, match: re.Match) -> str:
    start = match.end()
    next_header = ANY_ITEM_HEADER.search(text, pos=start)
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
