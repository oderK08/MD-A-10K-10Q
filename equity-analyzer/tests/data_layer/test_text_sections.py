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
