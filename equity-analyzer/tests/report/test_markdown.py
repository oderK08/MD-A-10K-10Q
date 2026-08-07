"""The model answers in markdown, the report is HTML."""

from __future__ import annotations

from equity_analyzer.report.markdown import markdown_to_html, truncate_words


def test_renders_headings_paragraphs_and_bullets():
    html = markdown_to_html(
        "## Verdict\n"
        "Plutot bullish.\n"
        "\n"
        "## Les declarations cles\n"
        "- Guidance relevee a 12%.\n"
        "- Capex maintenu.\n"
    )
    assert "<h2>Verdict</h2>" in html
    assert "<p>Plutot bullish.</p>" in html
    assert '<p class="bullet">· Guidance relevee a 12%.</p>' in html
    assert '<p class="bullet">· Capex maintenu.</p>' in html


def test_numbered_and_unicode_bullets_are_list_items_too():
    """
    The prompt asks for markdown but does not police which bullet glyph
    the model reaches for. A numbered list rendered as three bare
    paragraphs still reads, but it loses the indent that tells the eye
    these are parallel items.
    """
    for line in ("1. Premier point", "• Premier point", "* Premier point"):
        assert '<p class="bullet">· Premier point</p>' in markdown_to_html(line)


def test_bold_survives_but_everything_else_is_escaped():
    html = markdown_to_html('Marge **en hausse** sur R&D et <10% du CA.')
    assert "<strong>en hausse</strong>" in html
    assert "R&amp;D" in html
    assert "&lt;10%" in html


def test_html_in_a_quoted_sentence_cannot_reach_the_page_as_markup():
    """
    Page 1 quotes an earnings call verbatim. The text is not
    adversarial, but "<" and "&" are ordinary business English and an
    unescaped one corrupts the document silently.
    """
    html = markdown_to_html('Le CFO dit : "<b>growth</b> was strong".')
    assert "<b>" not in html
    assert "&lt;b&gt;" in html


def test_a_bullet_and_the_paragraph_after_it_stay_distinct():
    html = markdown_to_html("- Un point\n\nUn paragraphe apres la liste.")
    assert '<p class="bullet">· Un point</p>' in html
    assert "<p>Un paragraphe apres la liste.</p>" in html


def test_bullets_avoid_the_default_list_marker_entirely():
    """
    Regression on a bug only a rendered PDF shows: the <ul> marker came
    out as a missing glyph, because xhtml2pdf draws it from a character
    the embedded report font does not carry.
    """
    html = markdown_to_html("- Un point\n- Un autre")
    assert "<ul>" not in html and "<li>" not in html


def test_truncation_cuts_at_a_sentence_end():
    """
    The sentences being cut are quotations of what management said. Half
    a quotation presented as a whole one is a misquote, not a shortened
    one.
    """
    text = "Premiere phrase complete ici. Deuxieme phrase qui deborde du budget."
    clipped, truncated = truncate_words(text, max_words=7)

    assert truncated is True
    assert clipped.endswith(".")
    assert "Deuxieme" not in clipped


def test_text_within_budget_is_returned_untouched():
    assert truncate_words("Court.", max_words=50) == ("Court.", False)


def test_a_passage_with_no_full_stop_loses_its_tail_not_almost_all_of_itself():
    """
    The sentence boundary is only honoured in the second half of the
    budget. Without that guard, a passage whose only full stop is near
    the start would be cut back to that first sentence, silently losing
    most of a page.
    """
    text = "Fin. " + " ".join(f"mot{i}" for i in range(50))
    clipped, truncated = truncate_words(text, max_words=30)

    assert truncated is True
    assert len(clipped.split()) > 20


def test_truncation_keeps_the_answers_structure():
    """
    Regression, and it cost a page before it was caught. Truncating with
    " ".join(text.split()[:n]) joins every line into one, so the "##"
    that opens the first heading swallows the whole answer and the page
    renders as a single enormous heading instead of five sections. Found
    by measuring a real PDF, not by reading the code.
    """
    text = (
        "## Verdict\nPlutot bullish.\n\n"
        "## Face aux attentes\n" + " ".join(f"mot{i}." for i in range(60))
    )
    clipped, truncated = truncate_words(text, max_words=30)

    assert truncated is True
    assert clipped.count("\n") >= 3
    html = markdown_to_html(clipped)
    assert "<h2>Verdict</h2>" in html
    assert "<h2>Face aux attentes</h2>" in html
    assert html.count("<h2>") == 2


def test_a_blank_line_is_preserved_across_a_cut():
    text = "Premier paragraphe.\n\nDeuxieme paragraphe qui deborde largement du budget."
    clipped, _ = truncate_words(text, max_words=4)
    assert "\n\n" in clipped
