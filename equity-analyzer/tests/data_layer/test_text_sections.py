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

def test_item_header_must_have_its_title_on_the_same_line_as_its_number():
    """
    A table-of-contents row puts the item number and its title in
    separate cells, which html_to_text renders on separate lines. The
    header patterns used `\\s*` between the two, which matches newlines,
    so a TOC row was indistinguishable from a real heading -- and being
    earlier in the document, it won. Verbatim from the MSFT run's debug
    dump.
    """
    import re

    from equity_analyzer.data_layer.text_sections import ITEM_PATTERNS

    pattern = re.compile(ITEM_PATTERNS["item_1a_risk_factors"][0], re.IGNORECASE)
    toc_row = "Executive Officers Item 1A. \n\n\n\n Risk Factors Item 1B. \n\n Unresolved"
    real_heading = "ITEM 1A. RISK FACTORS \n Our operations are subject to various risks."

    assert pattern.search(toc_row) is None
    assert pattern.search(real_heading) is not None


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
    The same filing repeats "PART I \\n Item 1A" as a running header at
    every page break INSIDE the section. With `\\s` spanning the newline,
    that bare marker absorbed the following prose as its "title" and read
    as a real section boundary, cutting the section at the first page
    break. A real boundary has a title on the number's own line.
    """
    from equity_analyzer.data_layer.text_sections import ANY_ITEM_HEADER

    running_header = "our objectives. PART I \n Item 1A \n\n For all of these reasons, we may"
    assert ANY_ITEM_HEADER.search(running_header) is None
    # a genuine next-section heading is still found
    assert ANY_ITEM_HEADER.search("ITEM 1B. UNRESOLVED STAFF COMMENTS \n None.") is not None


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
