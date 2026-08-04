import pytest

from equity_analyzer.diff.text_diff import diff_text

PARA_A = "The company faces competition from larger rivals."
PARA_B = "Supply chain disruptions could affect our margins."
PARA_C = "We are exposed to foreign currency fluctuation risk."


def test_identical_text_is_fully_equal():
    text = f"{PARA_A}\n\n{PARA_B}"
    result = diff_text(text, text)

    assert result.similarity_ratio == pytest.approx(1.0)
    assert all(seg.kind == "equal" for seg in result.segments)
    assert result.added_word_count == 0
    assert result.removed_word_count == 0
    assert result.prior_word_count == result.current_word_count


def test_appended_paragraph_is_added_not_replace():
    prior = f"{PARA_A}\n\n{PARA_B}"
    current = f"{PARA_A}\n\n{PARA_B}\n\n{PARA_C}"

    result = diff_text(prior, current)
    kinds = [(seg.kind, seg.text) for seg in result.segments]

    assert kinds == [
        ("equal", PARA_A),
        ("equal", PARA_B),
        ("added", PARA_C),
    ]
    assert result.added_word_count == len(PARA_C.split())
    assert result.removed_word_count == 0


def test_removed_paragraph_is_detected():
    prior = f"{PARA_A}\n\n{PARA_B}\n\n{PARA_C}"
    current = f"{PARA_A}\n\n{PARA_C}"

    result = diff_text(prior, current)
    kinds = [(seg.kind, seg.text) for seg in result.segments]

    assert kinds == [
        ("equal", PARA_A),
        ("removed", PARA_B),
        ("equal", PARA_C),
    ]
    assert result.removed_word_count == len(PARA_B.split())
    assert result.added_word_count == 0


def test_replaced_paragraph_shows_both_removed_and_added():
    rewritten = "The company faces intense competition from many new entrants."
    prior = f"{PARA_A}\n\n{PARA_B}"
    current = f"{rewritten}\n\n{PARA_B}"

    result = diff_text(prior, current)
    kinds = [(seg.kind, seg.text) for seg in result.segments]

    assert ("removed", PARA_A) in kinds
    assert ("added", rewritten) in kinds
    assert ("equal", PARA_B) in kinds
    assert result.removed_word_count == len(PARA_A.split())
    assert result.added_word_count == len(rewritten.split())


def test_similarity_ratio_drops_for_mostly_new_text():
    prior = PARA_A
    current = f"{PARA_B}\n\n{PARA_C}"

    result = diff_text(prior, current)
    assert result.similarity_ratio < 0.5


def test_word_counts_reflect_whole_text_not_just_diffed_paragraphs():
    prior = f"{PARA_A}\n\n{PARA_B}"
    current = f"{PARA_A}\n\n{PARA_B}\n\n{PARA_C}"

    result = diff_text(prior, current)
    assert result.prior_word_count == len(f"{PARA_A} {PARA_B}".split())
    assert result.current_word_count == len(f"{PARA_A} {PARA_B} {PARA_C}".split())
