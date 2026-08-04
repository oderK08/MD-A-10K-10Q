import base64

import pytest

from equity_analyzer.report.charts import bar_chart_data_uri


def _decode_svg(data_uri: str) -> str:
    assert data_uri.startswith("data:image/svg+xml;base64,")
    encoded = data_uri.removeprefix("data:image/svg+xml;base64,")
    return base64.b64decode(encoded).decode("utf-8")


def test_returns_a_valid_svg_data_uri():
    uri = bar_chart_data_uri([10, 20, 30])
    svg = _decode_svg(uri)
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")


def test_bar_width_scales_to_the_max_value_present():
    svg = _decode_svg(bar_chart_data_uri([50, 100], width=300))
    # the second value (100) IS the max -> its fill bar should span the
    # full requested width; the first (50) should span half.
    assert 'width="300.0"' in svg
    assert 'width="150.0"' in svg


def test_explicit_max_value_overrides_the_series_max():
    # a Piotroski F-Score is always out of 9, regardless of what values
    # happen to be in this particular series.
    svg = _decode_svg(bar_chart_data_uri([3], max_value=9, width=90))
    # 3/9 of 90 = 30
    assert 'width="30.0"' in svg


def test_none_values_render_as_track_only_not_a_zero_bar():
    svg = _decode_svg(bar_chart_data_uri([None, 50], max_value=100, width=200))
    # exactly one filled bar (for the 50) plus two track rectangles
    # (one per row) -- None must not add its own (zero-width) fill rect.
    fill_rects = svg.count('width="100.0"')  # 50/100 of 200 = 100
    assert fill_rects == 1


def test_raises_on_empty_values():
    with pytest.raises(ValueError, match="at least one value"):
        bar_chart_data_uri([])


def test_zero_or_negative_max_falls_back_to_1_instead_of_dividing_by_zero():
    # shouldn't raise ZeroDivisionError even if every value is 0 or the
    # series is all non-positive
    uri = bar_chart_data_uri([0, 0], max_value=0)
    assert uri.startswith("data:image/svg+xml;base64,")


def test_labels_and_value_labels_are_drawn_inside_the_svg():
    svg = _decode_svg(bar_chart_data_uri(
        [50], labels=["2024"], value_labels=["$1.2M"], max_value=100,
    ))
    assert "<text" in svg
    assert ">2024<" in svg
    assert ">$1.2M<" in svg


def test_bar_width_unaffected_by_adding_labels():
    # the bar area itself must stay exactly `width` regardless of
    # whether labels/value_labels grow the total image width around it.
    plain = _decode_svg(bar_chart_data_uri([50], max_value=100, width=200))
    labeled = _decode_svg(bar_chart_data_uri(
        [50], labels=["Y"], value_labels=["V"], max_value=100, width=200,
    ))
    assert 'width="100.0"' in plain
    assert 'width="100.0"' in labeled


def test_labels_length_mismatch_raises():
    with pytest.raises(ValueError, match="labels must be the same length"):
        bar_chart_data_uri([1, 2], labels=["only one"])


def test_value_labels_length_mismatch_raises():
    with pytest.raises(ValueError, match="value_labels must be the same length"):
        bar_chart_data_uri([1, 2], value_labels=["only one"])


def test_label_text_is_xml_escaped():
    # a company/label containing "&" or "<" must not corrupt the SVG.
    svg = _decode_svg(bar_chart_data_uri([10], labels=["R&D <2024>"], max_value=10))
    assert "R&amp;D &lt;2024&gt;" in svg
    assert "R&D <2024>" not in svg
