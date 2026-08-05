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


def test_reordered_sentences_within_a_block_are_recognized_as_equal():
    """
    Real feedback from a Micron 10-K report: a sub-theme reshuffled the
    order of the same risk-factor sentences without changing their
    wording, and the diff showed the whole block as removed+re-added.
    That's the LCS ordering limitation this test guards against -- two
    sentences that swap places within the same block must both come back
    "equal", not "removed" + "added".
    """
    prior = f"{PARA_A}\n{PARA_B}\n{PARA_C}"
    current = f"{PARA_C}\n{PARA_A}\n{PARA_B}"

    result = diff_text(prior, current)

    assert all(seg.kind == "equal" for seg in result.segments)
    assert {seg.text for seg in result.segments} == {PARA_A, PARA_B, PARA_C}
    assert result.added_word_count == 0
    assert result.removed_word_count == 0


def test_reordered_pair_straddling_an_equal_block_is_recognized():
    """
    A plain two-item swap doesn't even produce a single "replace" opcode
    -- difflib emits insert/equal/delete instead, with the swapped pair
    on either side of an unrelated "equal" block (confirmed against
    difflib.SequenceMatcher directly). The fix has to work globally
    across the whole diff, not just within one opcode's block.
    """
    prior = f"{PARA_A}\n\n{PARA_B}"
    current = f"{PARA_B}\n\n{PARA_A}"

    result = diff_text(prior, current)

    assert all(seg.kind == "equal" for seg in result.segments)
    assert result.added_word_count == 0
    assert result.removed_word_count == 0


def test_reordering_does_not_hide_a_genuine_change_alongside_it():
    """
    A block can be partly reordered AND partly genuinely rewritten at the
    same time. The reordered sentences should still resolve to "equal";
    the truly new/removed content must still show up.
    """
    rewritten = "The company faces intense competition from many new entrants."
    prior = f"{PARA_A}\n{PARA_B}\n{PARA_C}"
    current = f"{PARA_C}\n{rewritten}\n{PARA_B}"

    result = diff_text(prior, current)
    kinds = [(seg.kind, seg.text) for seg in result.segments]

    assert ("equal", PARA_B) in kinds
    assert ("equal", PARA_C) in kinds
    assert ("removed", PARA_A) in kinds
    assert ("added", rewritten) in kinds
    assert result.removed_word_count == len(PARA_A.split())
    assert result.added_word_count == len(rewritten.split())


def test_near_duplicate_sentence_is_recognized_as_equal():
    """
    Real feedback from a Netflix report: a sentence showed up as BOTH
    removed and added, even though it read as exactly the same sentence.
    Two filings a year apart can differ in something invisible on the
    page (a non-breaking space, a smart vs. straight quote...) without
    the actual wording changing at all -- close enough that showing it
    as a real edit is noise, not signal.
    """
    base_words = [f"word{i}" for i in range(150)]
    prior_sentence = " ".join(base_words) + "."
    current_words = list(base_words)
    current_words[75] = "wordX"  # exactly one word out of 150 differs
    current_sentence = " ".join(current_words) + "."

    result = diff_text(prior_sentence, current_sentence)

    assert all(seg.kind == "equal" for seg in result.segments)
    assert result.added_word_count == 0
    assert result.removed_word_count == 0


def test_non_breaking_space_artifact_does_not_show_as_a_change():
    """
    A non-breaking space (U+00A0) isn't touched by the plain `[ \\t]+`
    whitespace collapsing in `_split_paragraphs` -- exactly the kind of
    invisible-on-the-page byte difference between two real filings that
    prompted this fix. The sentence must not show up as removed+added
    just because one copy used a regular space and the other didn't.
    """
    prior = "Our supply chain spans multiple countries and depend on third parties."
    current = prior.replace("multiple countries", "multiple countries")

    result = diff_text(prior, current)

    assert all(seg.kind == "equal" for seg in result.segments)
    assert result.added_word_count == 0
    assert result.removed_word_count == 0


def test_genuinely_different_sentences_still_show_as_a_real_change():
    """
    The near-duplicate tolerance must not swallow real content changes --
    a sentence with several actually-different words stays removed/added.
    """
    prior = "We expect revenue to grow modestly due to strong demand across our core markets this year."
    current = "We expect revenue to decline sharply due to weak demand across our core markets this year."

    result = diff_text(prior, current)
    kinds = {seg.kind for seg in result.segments}

    assert "removed" in kinds
    assert "added" in kinds
    assert result.removed_word_count > 0
    assert result.added_word_count > 0


def test_single_p_tag_with_line_wrapped_sentences_diffs_at_sentence_level():
    """
    Real SEC filings routinely put several sentences in ONE <p> tag,
    line-wrapped with bare newlines for source readability (see
    tests/fixtures/sample_10k.html). If only one sentence in that block
    changes, the diff must flag just that sentence -- not the whole
    block -- otherwise a one-word revenue update looks like the entire
    MD&A was rewritten.
    """
    prior = (
        "Revenue increased 12% year over year, driven primarily by strong\n"
        "demand in our core product lines.\n"
        "Gross margin improved by 150 basis points due to favorable input\n"
        "cost trends and manufacturing efficiencies.\n"
        "Operating expenses grew slower than revenue, reflecting continued\n"
        "discipline in discretionary spending."
    )
    current = (
        "Revenue increased 5% year over year, driven primarily by strong\n"
        "demand in our core product lines.\n"
        "Gross margin improved by 150 basis points due to favorable input\n"
        "cost trends and manufacturing efficiencies.\n"
        "Operating expenses grew slower than revenue, reflecting continued\n"
        "discipline in discretionary spending."
    )

    result = diff_text(prior, current)
    kinds = [seg.kind for seg in result.segments]

    # exactly one sentence replaced, the other two untouched -- not a
    # wholesale removal/re-addition of the entire block.
    assert kinds.count("removed") == 1
    assert kinds.count("added") == 1
    assert kinds.count("equal") == 2
    removed = next(seg.text for seg in result.segments if seg.kind == "removed")
    added = next(seg.text for seg in result.segments if seg.kind == "added")
    assert "12%" in removed
    assert "5%" in added
