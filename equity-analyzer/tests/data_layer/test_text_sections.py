from pathlib import Path

from equity_analyzer.data_layer.text_sections import extract_sections, html_to_text

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_extracts_real_risk_factors_not_toc_entry():
    html = (FIXTURES / "sample_10k.html").read_text()
    sections = extract_sections(html)
    assert sections.item_1a_risk_factors is not None
    # The TOC entry is just "Risk Factors" + a page number -- a handful
    # of characters. The real section is a full paragraph. If we grabbed
    # the TOC by mistake, this length check fails.
    assert len(sections.item_1a_risk_factors) > 200
    assert "intense competition" in sections.item_1a_risk_factors


def test_extracts_mdna_section():
    html = (FIXTURES / "sample_10k.html").read_text()
    sections = extract_sections(html)
    assert sections.item_7_mdna is not None
    assert "Revenue increased 12%" in sections.item_7_mdna


def test_mdna_extraction_survives_inline_cross_reference_to_item_1a():
    """
    Regression test for a real bug found on a real NVIDIA 10-K (not a
    hand-written fixture): the MD&A almost always opens with a sentence
    like "...should be read in conjunction with 'Item 1A. Risk
    Factors,' our Consolidated Financial Statements..." -- a reference
    to another item sitting mid-sentence. ANY_ITEM_HEADER, which only
    checks the shape "item <n>[letter]. <words>", used to treat that
    reference as the section's end boundary, cutting NVIDIA's real
    ~40,000-word MD&A down to 27 words. Since this exact boilerplate
    opens nearly every 10-K's MD&A, this wasn't NVIDIA-specific.
    """
    html = """
    <html><body>
    <p>Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations</p>
    <p>The following discussion should be read in conjunction with
    &#8220;Item 1A. Risk Factors,&#8221; our Consolidated Financial
    Statements and related Notes thereto.</p>
    <p>Revenue increased 12% year over year driven by strong demand
    across all segments, with gross margin expansion of 200 basis
    points.</p>
    <p>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</p>
    <p>We are exposed to market risk in the ordinary course of business.</p>
    </body></html>
    """
    sections = extract_sections(html)
    assert sections.item_7_mdna is not None
    # The cross-reference sentence must not have truncated the section --
    # content from *after* it (the real financial discussion) must still
    # be present, and content from the next real section must not be.
    assert "Revenue increased 12%" in sections.item_7_mdna
    assert "Item 1A. Risk Factors" in sections.item_7_mdna  # the reference itself is real MD&A prose
    assert "market risk in the ordinary course" not in sections.item_7_mdna


def test_extracts_controls_section():
    html = (FIXTURES / "sample_10k.html").read_text()
    sections = extract_sections(html)
    assert sections.item_9a_controls is not None
    assert "disclosure controls and procedures were effective" in sections.item_9a_controls


def test_10k_risk_factors_not_flagged_as_boilerplate():
    html = (FIXTURES / "sample_10k.html").read_text()
    sections = extract_sections(html)
    assert sections.is_risk_factors_boilerplate is False


def test_10q_boilerplate_risk_factors_detected():
    html = (FIXTURES / "sample_10q_boilerplate.html").read_text()
    sections = extract_sections(html)
    assert sections.item_1a_risk_factors is not None
    assert sections.is_risk_factors_boilerplate is True


def test_10q_mdna_extracted_via_item_2_pattern():
    html = (FIXTURES / "sample_10q_boilerplate.html").read_text()
    sections = extract_sections(html)
    assert sections.item_7_mdna is not None
    assert "softer demand" in sections.item_7_mdna


def test_html_to_text_strips_tags_and_keeps_paragraphs():
    html = "<p>First paragraph.</p><p>Second paragraph.</p>"
    text = html_to_text(html)
    assert "<p>" not in text
    assert "First paragraph." in text
    assert "Second paragraph." in text


def test_html_to_text_decodes_common_entities():
    html = "<p>Company&rsquo;s results &amp; outlook</p>"
    text = html_to_text(html)
    # html.unescape decodes to the real Unicode curly apostrophe (U+2019),
    # not an ASCII "'" -- that's the semantically correct decoding, and
    # nothing downstream (word-boundary regexes, alpha-only tokenization
    # for sentiment) depends on it being ASCII.
    assert "Company’s results & outlook" in text


def test_html_to_text_decodes_numeric_entities():
    """
    A real Coca-Cola 10-K uses the NUMERIC form "&#160;" (not the named
    "&nbsp;") between "Item 7." and "Management's Discussion" --
    undecoded, that literal text isn't whitespace to the header regex,
    so the section silently failed to extract. html.unescape handles
    both forms; our old hand-rolled entity list only handled a few named
    ones.
    """
    html = "<p>ITEM 7.&#160;&#160;MANAGEMENT&#8217;S DISCUSSION</p>"
    text = html_to_text(html)
    # the double NBSP collapses to a single space, same as double regular
    # spaces would -- what matters is that it's real whitespace at all.
    assert "ITEM 7. MANAGEMENT’S DISCUSSION" in text
    assert "&#160;" not in text
    assert "\xa0" not in text


def test_html_to_text_rejoins_word_split_by_inline_tag():
    """
    A real Microsoft 10-K wraps a fragment of a word in its own tag for
    layout purposes, e.g. "RIS<span>K</span> FACTORS" -- blindly turning
    every tag into a space broke "RISK" into "RIS" + "K", so
    "risk\\s+factors" no longer matched anywhere in the document.
    """
    html = '<p>ITEM 1A. RIS<span class="kern">K</span> FACTORS</p>'
    text = html_to_text(html)
    assert "RISK FACTORS" in text


def test_html_to_text_still_separates_adjacent_table_cells():
    """
    The word-rejoining fix above must not glue together content from
    genuinely separate elements -- adjacent table cells with no space in
    the source between </td> and <td> must still end up space-separated.
    """
    html = "<table><tr><td>Revenue</td><td>1000</td></tr></table>"
    text = html_to_text(html)
    assert "Revenue 1000" in text
    assert "Revenue1000" not in text


def test_html_to_text_strips_page_furniture_mid_sentence():
    """
    Regression test for a real bug found on a real NVIDIA 10-K: a lone
    page number and a repeated "Table of Contents" jump-link marker land
    INSIDE the extracted plaintext at every page boundary, with no other
    whitespace cue -- verbatim: "...cause our stock \n 13 \n\n Table of
    Contents \n\n price to decline." Left in, "13" and "Table of
    Contents" become their own spurious diff/sentiment tokens, and the
    blank line the furniture introduces fools the diff module's
    paragraph splitter into treating one real sentence as two.
    """
    html = (
        "<p>The following risks could harm our business, which could "
        "cause our stock</p><p>13</p><p></p><p>Table of Contents</p>"
        "<p></p><p>price to decline.</p>"
    )
    text = html_to_text(html)
    assert "13" not in text
    assert "Table of Contents" not in text
    assert "cause our stock price to decline." in text


def test_html_to_text_leaves_a_genuine_bare_number_sentence_fragment_alone():
    """
    The page-furniture strip must only fire on a page number sitting
    ALONE on its own line -- a number that's part of real prose (e.g. a
    list numbering a filer actually wrote out) shouldn't be touched.
    Table of Contents boilerplate genuinely never appears embedded in a
    real sentence like this, so this guards specifically against the
    bare-page-number half of the pattern being too aggressive.
    """
    html = "<p>We identified 13 distinct supply chain risks this year.</p>"
    text = html_to_text(html)
    assert "We identified 13 distinct supply chain risks this year." in text


# --- Regression tests for a real Microsoft 10-K run (2026-08-07) that
# extracted Item 1A as 22 words. Two independent causes, both visible in
# that run's own debug dump, reproduced verbatim here. ---

def test_a_heading_whose_number_and_title_are_on_separate_lines_is_matched():
    """
    Filers genuinely differ here and BOTH shapes are real headings:

        Microsoft   "ITEM 1A. RISK FACTORS"
        AAOI        "Item 1A. \\n \\n Risk Factors"

    An earlier fix banned line breaks between the number and the title,
    to stop a table-of-contents row being mistaken for a heading. That
    was the wrong lever. Verbatim from a real AAOI run's debug dump:
    "...furnishing them to, the SEC. \\n\\n \\n \\n Item 1A. \\n \\n Risk
    Factors \\n\\n Investing in our common s..." -- Item 1A and Item 7
    both became unextractable, and the run produced a report with no
    filing text in it whatsoever.
    """
    import re

    from equity_analyzer.data_layer.text_sections import ITEM_PATTERNS

    pattern = re.compile(ITEM_PATTERNS["item_1a_risk_factors"][0], re.IGNORECASE)
    assert pattern.search("Item 1A. \n \n Risk Factors \n\n Investing in our common") is not None
    assert pattern.search("ITEM 1A. RISK FACTORS \n Our operations are subject to risks.") is not None


def test_the_toc_row_loses_to_the_real_heading_on_content_not_on_layout():
    """
    The TOC is separated from the real heading by what follows each of
    them, not by forbidding a layout real filings use. A TOC entry is
    followed within a few characters by the next TOC entry; a real
    section by pages of prose.

    Both rows below match the header pattern -- that is the point. The
    ranking has to pick the right one.
    """
    from equity_analyzer.data_layer.text_sections import (
        ITEM_PATTERNS,
        _find_best_header_match,
    )

    body = " ".join(f"Risk sentence number {i}." for i in range(60))
    text = (
        "Business Item 1A. \n \n Risk Factors Item 1B. \n \n Unresolved Staff Comments\n"
        f"Item 1A. \n \n Risk Factors \n\n {body}\n"
        "Item 1B. \n \n Unresolved Staff Comments\n"
    )
    match = _find_best_header_match(text, ITEM_PATTERNS["item_1a_risk_factors"])
    assert match is not None
    # the real one, i.e. not the occurrence inside the first (TOC) line
    assert match.start() > text.index("\n")


def test_item_header_matches_a_word_split_by_the_printer():
    """
    Same MSFT filing, same debug dump: the real heading reads "ITEM 1A.
    RIS K FACTORS" -- the printer split the word, and the split survives
    into the extracted text as a space. A plain `risk` never matched it,
    so the only thing left matching was the TOC row.
    """
    import re

    from equity_analyzer.data_layer.text_sections import ITEM_PATTERNS

    pattern = re.compile(ITEM_PATTERNS["item_1a_risk_factors"][0], re.IGNORECASE)
    assert pattern.search("ITEM 1A. RIS K FACTORS \n Our operations") is not None
    # and the ordinary spelling still matches
    assert pattern.search("Item 1A. Risk Factors \n Our operations") is not None


def test_a_repeated_bare_running_page_header_is_not_a_section_boundary():
    """
    The same Microsoft filing repeats "PART I \\n Item 1A" as a running
    header at every page break INSIDE Item 1A. That bare marker binds to
    whatever prose follows it and reads as a section boundary, cutting
    the section at the first page break.

    An item is never the boundary of ITSELF -- the next section always
    carries a different number -- so the fix is to exclude the section's
    own number while scanning for its end. The previous fix (requiring
    the title on the number's own line) also stopped this, but broke
    every filer that splits the two across lines; see
    test_a_heading_whose_number_and_title_are_on_separate_lines_is_matched.
    """
    from equity_analyzer.data_layer.text_sections import _find_next_real_header

    text = "PART I \n Item 1A \n\n For all of these reasons, we may be harmed.\n"
    # scanning for the end of Item 1A: its own running header is not an end
    assert _find_next_real_header(text, 0, exclude_number="1a") is None
    # ...but it IS a real boundary for any other section
    assert _find_next_real_header(text, 0, exclude_number="7") is not None
    # and a genuine next-section heading is still found
    assert _find_next_real_header(
        "ITEM 1B. UNRESOLVED STAFF COMMENTS \n None.", 0, exclude_number="1a"
    ) is not None


def test_microsoft_shaped_filing_extracts_the_real_section_not_the_toc():
    """
    End-to-end on a filing reproducing all three MSFT quirks at once:
    a TOC row, a printer-split heading, and running page headers inside
    the section. Before the fix this returned 22 words.
    """
    from equity_analyzer.data_layer.text_sections import extract_sections

    body = " ".join(f"Risk sentence number {i} about our operations." for i in range(40))
    html = (
        "<p>Business Information about our Executive Officers Item 1A.</p>"
        "<p>Risk Factors Item 1B.</p><p>Unresolved Staff Comments</p>"
        "<p>PART I</p><p>Item 1A</p>"
        "<p>ITEM 1A. RIS<span>K</span> FACTORS</p>"
        f"<p>{body}</p>"
        "<p>PART I</p><p>Item 1A</p>"
        f"<p>{body}</p>"
        "<p>ITEM 1B. UNRESOLVED STAFF COMMENTS</p><p>None.</p>"
    )
    sections = extract_sections(html)
    assert sections.item_1a_risk_factors is not None
    word_count = len(sections.item_1a_risk_factors.split())
    assert word_count > 200, f"section truncated to {word_count} words"
    # it must stop at the real next section, not run past it
    assert "UNRESOLVED STAFF COMMENTS" not in sections.item_1a_risk_factors


def test_aaoi_shaped_filing_extracts_both_sections():
    """
    End-to-end reproduction of a real AAOI 10-K, from the run that
    produced a report with no filing text at all. Every item number sits
    on its own line, separated from its title -- in the TOC and in the
    body alike -- and the TOC rows run each title straight into the next
    item number.

    Both Item 1A and Item 7 came back None on the real run, which the
    pipeline then handed to the summariser as an empty fact sheet.
    """
    from equity_analyzer.data_layer.text_sections import extract_sections

    risk_body = " ".join(f"Risk sentence number {i} about our operations." for i in range(40))
    mdna_body = " ".join(f"Revenue discussion sentence number {i}." for i in range(40))
    html = (
        "<div>Part I Item 1.</div><div>Business Item 1A.</div>"
        "<div>Risk Factors Item 1B.</div><div>Unresolved Staff Comments Item 7.</div>"
        "<div>Management's Discussion and Analysis of Financial Condition</div>"
        "<p>These reports are available on filing them with, or furnishing them to, the SEC.</p>"
        "<div>Item 1A.</div><div>Risk Factors</div>"
        f"<p>Investing in our common stock involves risk. {risk_body}</p>"
        "<div>Item 1B.</div><div>Unresolved Staff Comments</div><p>None.</p>"
        "<div>Item 6.</div><div>[Reserved]</div>"
        "<div>Item 7.</div><div>Management's Discussion and Analysis of Financial Condition</div>"
        f"<p>{mdna_body}</p>"
        "<div>Item 7A.</div><div>Quantitative and Qualitative Disclosures about Market Risk</div>"
        "<p>Not applicable.</p>"
    )
    sections = extract_sections(html)

    assert sections.item_1a_risk_factors is not None
    assert len(sections.item_1a_risk_factors.split()) > 200
    assert "Unresolved Staff Comments" not in sections.item_1a_risk_factors

    assert sections.item_7_mdna is not None
    assert len(sections.item_7_mdna.split()) > 200
    # stops at Item 7A rather than running to the end of the document
    assert "Not applicable" not in sections.item_7_mdna
