from pathlib import Path

import pytest

from equity_analyzer.report.pdf_renderer import render_pdf, save_pdf

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
