"""Errors for the report module."""

from __future__ import annotations


class ReportError(Exception):
    """Base class for all report-generation errors."""


class PdfRenderError(ReportError):
    """xhtml2pdf failed to render the report HTML into a PDF."""
