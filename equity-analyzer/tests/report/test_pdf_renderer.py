from pathlib import Path

import pytest

from equity_analyzer.report.pdf_renderer import page_count, render_pdf, save_pdf

SIMPLE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Test</title></head>
<body><h1>Hello</h1><p>A simple test report.</p></body></html>
"""


def test_render_pdf_produces_valid_pdf_bytes():
    pdf_bytes = render_pdf(SIMPLE_HTML)
    assert pdf_bytes[:5] == b"%PDF-"
    assert len(pdf_bytes) > 100


def test_save_pdf_writes_a_file(tmp_path):
    output_path = tmp_path / "report.pdf"
    save_pdf(SIMPLE_HTML, output_path)

    assert output_path.exists()
    assert output_path.read_bytes()[:5] == b"%PDF-"


def test_save_pdf_accepts_string_path(tmp_path):
    output_path = str(tmp_path / "report.pdf")
    save_pdf(SIMPLE_HTML, output_path)
    assert Path(output_path).exists()


def test_save_pdf_returns_the_page_count_it_actually_wrote(tmp_path):
    """
    Counted from the written file, not echoed back from `max_pages`.

    The run log announces this number, and `render_pdf_fitted` keeps an
    overrunning render rather than dropping a row, so a budget of one
    page against three pages of content has to come back as three. A
    caller that reported its own argument would print "1 page" over a
    three page document, which is the one thing this project treats as
    worse than an ugly report.
    """
    long_html = SIMPLE_HTML.replace(
        "<p>A simple test report.</p>",
        "".join(f"<p>Ligne {i} de contenu qui remplit la page.</p>"
                for i in range(220)),
    )
    output_path = tmp_path / "long.pdf"
    pages = save_pdf(long_html, output_path, max_pages=1)

    assert pages > 1
    assert pages == page_count(output_path.read_bytes())
