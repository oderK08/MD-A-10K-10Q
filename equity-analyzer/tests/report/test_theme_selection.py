"""
Tests for the sub-theme selection pass (report/theme_selection.py).

Like every other network-touching module in this project, the real
Claude call is never made here: the HTTP layer is stubbed so `pytest`
stays offline, free and deterministic.
"""

from unittest.mock import patch

from equity_analyzer.diff.grouped_diff import (
    DiffGroup,
    GroupedTextDiffResult,
    apply_theme_selection,
)
from equity_analyzer.diff.text_diff import TextDiffResult
from equity_analyzer.report import theme_selection as ts


def _themes_text(count, words=40):
    filler = " ".join(f"word{i}" for i in range(words))
    return "\n\n".join(f"Theme {chr(65 + i)} Heading Here\n\n{filler}" for i in range(count))


class _FakeResponse:
    status_code = 200

    def __init__(self, text):
        self._text = text

    def json(self):
        return {"content": [{"type": "text", "text": self._text}]}


def _stub(answer, status=200):
    class _Resp(_FakeResponse):
        pass

    _Resp.status_code = status
    if status != 200:
        _Resp.text = answer
    return lambda *args, **kwargs: _Resp(answer)


def test_list_subthemes_skips_the_preamble_and_tiny_sections():
    text = (
        "Some preamble prose before any heading appears in this section.\n\n"
        "A Real Sub Theme\n\n" + " ".join(f"word{i}" for i in range(50)) + "\n\n"
        "A Stub Heading\n\ntoo short"
    )
    headings = [t.heading for t in ts.list_subthemes(text)]
    assert headings == ["A Real Sub Theme"]


def test_the_model_judges_relevance_even_when_everything_would_fit():
    """
    Selecting is not capping. With a key, the model is asked which
    sub-themes matter for THIS company every time -- including when
    there are already fewer of them than the cap. A filing with three
    sub-sections may well have only one worth reading, and keeping all
    three just because they fit is the volume-driven behaviour this
    pass exists to replace.
    """
    answer = '["Theme B Heading Here"]'
    with patch.object(ts.requests, "post", _stub(answer)):
        selection = ts.select_key_subthemes(
            _themes_text(3), company="Test Co", api_key="sk-fake"
        )
    assert selection.ai_selected
    assert selection.headings == ["Theme B Heading Here"]


def test_no_api_call_when_there_is_only_one_subtheme():
    """One option is not a choice -- don't pay for it."""
    def _explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("nothing to select between")

    with patch.object(ts.requests, "post", _explode):
        selection = ts.select_key_subthemes(
            _themes_text(1), company="Test Co", api_key="sk-fake"
        )
    assert len(selection.headings) == 1
    assert not selection.ai_selected


def test_the_model_is_given_headings_only_not_sizes():
    """
    Sending "(N mots)" next to each heading quietly reintroduces the
    volume bias this pass removes: shown a size, the model drifts toward
    the big sections. Relevance to the company is the only criterion, so
    size must not reach it.
    """
    captured = {}

    def _capture(*args, **kwargs):
        captured["content"] = kwargs["json"]["messages"][0]["content"]
        return _FakeResponse('["Theme A Heading Here"]')

    with patch.object(ts.requests, "post", _capture):
        ts.select_key_subthemes(_themes_text(12), company="Test Co", api_key="sk-fake")

    assert "Theme A Heading Here" in captured["content"]
    assert "mots" not in captured["content"]
    assert "40" not in captured["content"]


def test_selection_uses_the_model_and_preserves_its_ranking():
    answer = '["Theme C Heading Here", "Theme A Heading Here"]'
    with patch.object(ts.requests, "post", _stub(answer)):
        selection = ts.select_key_subthemes(
            _themes_text(14), company="Test Co", api_key="sk-fake"
        )
    assert selection.ai_selected
    # order is the model's ranking, not document order
    assert selection.headings == ["Theme C Heading Here", "Theme A Heading Here"]


def test_a_hallucinated_heading_is_dropped_not_passed_through():
    """
    The model may only CHOOSE from the list it was given. A heading it
    invented (or silently reworded) must never enter the pipeline --
    otherwise the diff would be asked for a sub-theme that doesn't exist
    in the filing.
    """
    answer = '["Theme B Heading Here", "Totally Invented Theme", "Theme A Heading Here"]'
    with patch.object(ts.requests, "post", _stub(answer)):
        selection = ts.select_key_subthemes(
            _themes_text(14), company="Test Co", api_key="sk-fake"
        )
    assert selection.headings == ["Theme B Heading Here", "Theme A Heading Here"]


def test_selection_is_capped_even_if_the_model_returns_more():
    answer = "[" + ", ".join(f'"Theme {chr(65 + i)} Heading Here"' for i in range(14)) + "]"
    with patch.object(ts.requests, "post", _stub(answer)):
        selection = ts.select_key_subthemes(
            _themes_text(14), company="Test Co", api_key="sk-fake"
        )
    assert len(selection.headings) == ts.MAX_SELECTED_THEMES


def test_unparseable_answer_falls_back_and_says_so():
    with patch.object(ts.requests, "post", _stub("désolé, je ne peux pas répondre")):
        selection = ts.select_key_subthemes(
            _themes_text(14), company="Test Co", api_key="sk-fake"
        )
    assert not selection.ai_selected
    assert len(selection.headings) == ts.MAX_SELECTED_THEMES
    # the report must be able to state that no AI actually chose these
    assert "indisponible" in selection.reason


def test_no_api_key_falls_back_without_any_network_call():
    def _explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("no key means no call")

    with patch.object(ts.requests, "post", _explode):
        selection = ts.select_key_subthemes(
            _themes_text(14), company="Test Co", api_key=None
        )
    assert not selection.ai_selected
    assert "non demandée" in selection.reason


def test_no_subthemes_returns_an_empty_selection_with_a_reason():
    selection = ts.select_key_subthemes(
        "Just flat prose with no headings at all in it.", company="Test Co", api_key=None
    )
    assert selection.headings == []
    assert "aucune sous-section" in selection.reason


def _group(heading):
    empty = TextDiffResult(
        segments=[], similarity_ratio=1.0, prior_word_count=0,
        current_word_count=0, added_word_count=0, removed_word_count=0,
    )
    return DiffGroup(heading=heading, status="matched", diff=empty)


def test_apply_theme_selection_marks_without_dropping():
    """
    Unselected sub-themes stay in `groups` (still counted, still
    reportable in the detail document) -- selection is a marking, not a
    filter.
    """
    result = GroupedTextDiffResult(
        overall=_group("x").diff,
        groups=[_group("A"), _group("B"), _group("C")],
    )
    marked = apply_theme_selection(result, ["C", "A"])

    assert len(marked.groups) == 3
    assert [g.heading for g in marked.selected_groups] == ["C", "A"]
    assert [g.heading for g in marked.groups if not g.selected] == ["B"]


def test_apply_empty_selection_selects_nothing_rather_than_everything():
    """
    An empty selection means "the selector found nothing worth
    detailing" -- a real answer that must not be silently inverted into
    "keep them all".
    """
    result = GroupedTextDiffResult(
        overall=_group("x").diff, groups=[_group("A"), _group("B")]
    )
    marked = apply_theme_selection(result, [])
    assert marked.selected_groups == []


def test_seravek_is_never_referenced():
    """
    Seravek was requested, then explicitly dropped: it's a proprietary
    macOS-only font that can't be embedded in a CI-generated PDF. It
    must not linger as a CSS fallback either -- that would render the
    same report in one face on a Mac and another everywhere else.
    """
    from equity_analyzer.report import fonts
    from equity_analyzer.report.html_renderer import _css

    assert "seravek" not in fonts.body_font_stack().lower()
    assert "seravek" not in _css().lower()


def test_font_falls_back_cleanly_when_the_font_file_is_missing(tmp_path, monkeypatch):
    """
    The report uses Lato -- but a machine without it must still render,
    in the generic sans, rather than declaring a family with no faces
    behind it.
    """
    from equity_analyzer.report import fonts

    monkeypatch.setattr(fonts, "_CANDIDATE_DIRS", [tmp_path])
    assert fonts.font_face_css() == ""
    # the stack still ends in a real generic family
    assert fonts.body_font_stack().endswith("sans-serif")


def test_font_face_css_embeds_a_real_file_when_present():
    """
    Guards the actual mechanism: xhtml2pdf ignores the system font
    database, so only an @font-face rule pointing at a real path gets
    the font into the PDF. Skips where the font isn't installed rather
    than failing -- the fallback above covers that case.
    """
    import pytest

    from equity_analyzer.report import fonts

    if fonts._find("Lato-Regular.ttf") is None:
        pytest.skip("Lato not installed on this machine")
    css = fonts.font_face_css()
    assert "@font-face" in css
    assert ".ttf" in css
    assert fonts.BODY_FONT_FAMILY in css
