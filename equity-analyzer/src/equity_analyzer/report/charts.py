"""
Minimal SVG bar-chart generator for the PDF report.

Why SVG specifically: plain CSS (nested `<div>`s with percentage or even
fixed-unit widths meant to render as a filled bar) does NOT render
reliably in xhtml2pdf -- verified directly by generating test PDFs and
visually inspecting the rendered pages before committing to a design,
not assumed from documentation. Nested block/inline-block width
percentages were silently ignored, producing a full-width bar or no
fill at all regardless of the intended proportion.

An embedded SVG image (as a `data:image/svg+xml;base64,...` URI inside
an `<img>` tag) renders pixel-accurately, confirmed the same way --
xhtml2pdf bundles `svglib` specifically for this, so it's not a hack,
it's the supported path for anything more precise than text and tables.

Row labels and value labels are drawn AS SVG <text> elements inside the
same image, rather than as separate HTML text next to it -- also
verified by rendering: cross-technology alignment (an HTML label column
next to a separately-sized image) is exactly the kind of thing that
silently drifts by a pixel or a line in a constrained renderer like this
one. Keeping everything in one coordinate system inside the SVG makes
misalignment structurally impossible instead of something to get right
by trial and error.
"""

from __future__ import annotations

import base64
from typing import Optional, Sequence

DEFAULT_BAR_COLOR = "#2a6f97"
DEFAULT_TRACK_COLOR = "#e8e8e8"


def bar_chart_data_uri(
    values: Sequence[Optional[float]],
    *,
    labels: Optional[Sequence[str]] = None,
    value_labels: Optional[Sequence[str]] = None,
    max_value: Optional[float] = None,
    width: int = 300,
    label_width: int = 40,
    value_width: int = 50,
    bar_height: int = 12,
    gap: int = 8,
    color: str = DEFAULT_BAR_COLOR,
    track_color: str = DEFAULT_TRACK_COLOR,
    font_size: int = 9,
) -> str:
    """
    Builds a horizontal bar chart (one bar per value, top to bottom) as
    an inline SVG, returned as a data: URI ready to drop into an
    `<img src="...">` tag. `width` is always the bar area's own pixel
    width; pass `labels` and/or `value_labels` (same length as `values`)
    to draw a row label to the left and a value label to the right of
    each bar, inside the same image -- the total image width grows by
    `label_width`/`value_width` accordingly, but the bar area itself
    stays exactly `width` regardless.

    `None` values render as an empty (track only) row rather than a
    zero-length bar, so "no data" stays visually distinct from "value of
    zero".

    `max_value` lets the caller fix the scale explicitly (e.g. 9 for a
    Piotroski F-Score, always 0-9 regardless of what's in `values`);
    omit it to scale to the largest value actually present.
    """
    if not values:
        raise ValueError("need at least one value to build a bar chart")
    if labels is not None and len(labels) != len(values):
        raise ValueError("labels must be the same length as values")
    if value_labels is not None and len(value_labels) != len(values):
        raise ValueError("value_labels must be the same length as values")

    present = [v for v in values if v is not None]
    effective_max = max_value if max_value is not None else (max(present) if present else 1)
    if not effective_max or effective_max <= 0:
        effective_max = 1

    left_margin = label_width if labels is not None else 0
    right_margin = value_width if value_labels is not None else 0
    total_width = left_margin + width + right_margin
    row_height = bar_height + gap
    total_height = len(values) * row_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" '
        f'height="{total_height}" font-family="Helvetica, Arial, sans-serif">'
    ]
    for i, value in enumerate(values):
        row_top = i * row_height
        bar_y = row_top + (gap // 2)
        text_y = bar_y + bar_height - 2  # roughly vertically centered on the bar

        if labels is not None:
            parts.append(
                f'<text x="0" y="{text_y}" font-size="{font_size}">{_xml_escape(labels[i])}</text>'
            )
        parts.append(
            f'<rect x="{left_margin}" y="{bar_y}" width="{width}" height="{bar_height}" '
            f'fill="{track_color}"/>'
        )
        if value is not None:
            fill_width = max(0.0, min(float(width), width * value / effective_max))
            parts.append(
                f'<rect x="{left_margin}" y="{bar_y}" width="{fill_width:.1f}" '
                f'height="{bar_height}" fill="{color}"/>'
            )
        if value_labels is not None:
            value_x = left_margin + width + 6
            parts.append(
                f'<text x="{value_x}" y="{text_y}" font-size="{font_size}">'
                f'{_xml_escape(value_labels[i])}</text>'
            )
    parts.append("</svg>")
    svg = "".join(parts)
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
