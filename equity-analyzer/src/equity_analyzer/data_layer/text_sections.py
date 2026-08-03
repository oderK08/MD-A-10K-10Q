"""
Extracts the sections we care about (Risk Factors, MD&A, Controls) from the
raw HTML of a 10-K or 10-Q.

Two rigor problems solved here, both real and both silent-failure-prone
if ignored:

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
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
_WHITESPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def html_to_text(html: str) -> str:
    """Strips HTML tags to plain text while keeping paragraph breaks."""
    # Turn common block-level closings into newlines before stripping tags,
    # so paragraphs don't get smashed together.
    html = re.sub(r"(?i)</(p|div|tr|br|li)\s*>", "\n", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = _TAG_RE.sub(" ", html)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    # Decode a small set of common HTML entities without pulling in a
    # full HTML parser dependency for this normalization step.
    for entity, char in [("&amp;", "&"), ("&nbsp;", " "), ("&#8217;", "'"),
                         ("&#8220;", '"'), ("&#8221;", '"'), ("&rsquo;", "'")]:
        text = text.replace(entity, char)
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
