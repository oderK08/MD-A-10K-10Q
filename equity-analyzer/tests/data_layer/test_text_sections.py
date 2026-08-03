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
    assert "Company's results & outlook" in text
