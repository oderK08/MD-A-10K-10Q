from equity_analyzer.diff.grouped_diff import diff_text_grouped


def test_no_headings_degrades_to_a_single_unheaded_group():
    """
    The common case -- a filing whose Item 1A/Item 7 has no internal
    sub-headings at all -- must produce exactly one group with an empty
    heading, whose diff is identical to the flat `overall` diff. This is
    what lets the renderer treat it as "no grouping happened" rather
    than needing a separate code path.
    """
    prior = "Revenue increased 5% year over year.\n\nMargins were flat."
    current = "Revenue increased 12% year over year.\n\nMargins were flat."

    result = diff_text_grouped(prior, current)

    assert len(result.groups) == 1
    assert result.groups[0].heading == ""
    assert result.groups[0].status == "matched"
    assert result.groups[0].diff.segments == result.overall.segments


def test_matched_sub_theme_headings_are_diffed_independently():
    prior = (
        "Risks Related to Demand\n"
        "Long lead times could harm our business.\n\n"
        "Risks Related to Regulation\n"
        "New export controls could restrict our sales."
    )
    current = (
        "Risks Related to Demand\n"
        "Long lead times and supply shortages could harm our business.\n\n"
        "Risks Related to Regulation\n"
        "New export controls could restrict our sales."
    )

    result = diff_text_grouped(prior, current)
    headings = [g.heading for g in result.groups]
    assert "Risks Related to Demand" in headings
    assert "Risks Related to Regulation" in headings

    demand_group = next(g for g in result.groups if g.heading == "Risks Related to Demand")
    assert demand_group.status == "matched"
    # the sentence was reworded (clause inserted), not wholly replaced --
    # recognized as one "modified" segment, not removed+added.
    assert any(seg.kind == "modified" for seg in demand_group.diff.segments)

    regulation_group = next(g for g in result.groups if g.heading == "Risks Related to Regulation")
    assert regulation_group.status == "matched"
    assert not any(seg.kind in ("added", "removed", "modified") for seg in regulation_group.diff.segments)


def test_a_sub_theme_present_only_in_current_year_is_reported_as_added():
    prior = "Risks Related to Demand\nLong lead times could harm our business."
    current = (
        "Risks Related to Demand\n"
        "Long lead times could harm our business.\n\n"
        "Risks Related to Cybersecurity\n"
        "A data breach could disrupt our operations."
    )

    result = diff_text_grouped(prior, current)
    cyber_group = next(g for g in result.groups if g.heading == "Risks Related to Cybersecurity")
    assert cyber_group.status == "added"
    assert all(seg.kind == "added" for seg in cyber_group.diff.segments)


def test_a_sub_theme_present_only_in_prior_year_is_reported_as_removed():
    prior = (
        "Risks Related to Demand\n"
        "Long lead times could harm our business.\n\n"
        "Risks Related to Litigation\n"
        "We are involved in ongoing litigation."
    )
    current = "Risks Related to Demand\nLong lead times could harm our business."

    result = diff_text_grouped(prior, current)
    litigation_group = next(g for g in result.groups if g.heading == "Risks Related to Litigation")
    assert litigation_group.status == "removed"
    assert all(seg.kind == "removed" for seg in litigation_group.diff.segments)


def test_overall_stats_match_the_flat_diff_regardless_of_grouping():
    """
    `overall` must be identical to what plain diff_text() would have
    returned -- grouping is a rendering-time view on top, not a
    different computation. Existing consumers of the aggregate stats
    (executive summary, top-of-section "Similarité X%" line) must keep
    working unchanged.
    """
    from equity_analyzer.diff.text_diff import diff_text

    prior = "Risks Related to Demand\nLong lead times could harm our business."
    current = (
        "Risks Related to Demand\n"
        "Long lead times could harm our business.\n\n"
        "Risks Related to Cybersecurity\n"
        "A data breach could disrupt our operations."
    )

    result = diff_text_grouped(prior, current)
    flat = diff_text(prior, current)
    assert result.overall.similarity_ratio == flat.similarity_ratio
    assert result.overall.segments == flat.segments


def test_real_nvidia_shaped_structure_groups_correctly():
    """
    Regression test reproducing the real structure found in a real
    NVIDIA 10-K's Item 1A: a short bulleted "Risk Factors Summary" digest
    (each heading followed immediately by a one-line bullet) followed
    later by the SAME heading text again, this time followed by the real,
    much longer detailed section. The duplicate heading text must not
    confuse matching -- both occurrences are matched independently, in
    document order, against the corresponding occurrence in the other
    year.
    """
    def make(manufacturing_detail):
        return (
            "Risk Factors Summary\n"
            "Risks Related to Manufacturing\n"
            "• Long lead times could harm our business.\n\n"
            "Risk Factors\n"
            "Risks Related to Manufacturing\n"
            f"{manufacturing_detail}"
        )

    prior = make("Long manufacturing lead times and supply constraints could harm our business.")
    current = make("Long manufacturing lead times, supply constraints, and new export controls could harm our business.")

    result = diff_text_grouped(prior, current)
    manufacturing_groups = [g for g in result.groups if g.heading == "Risks Related to Manufacturing"]
    assert len(manufacturing_groups) == 2  # the summary bullet AND the real section, matched independently

    # the summary bullet occurrence (short, unchanged between years)
    assert manufacturing_groups[0].status == "matched"
    assert not any(seg.kind in ("added", "removed", "modified") for seg in manufacturing_groups[0].diff.segments)

    # the real detailed occurrence (rewritten -- "export controls" is new)
    assert manufacturing_groups[1].status == "matched"
    assert any(seg.kind == "modified" for seg in manufacturing_groups[1].diff.segments)


def test_wrapped_prose_is_not_promoted_to_a_sub_theme():
    """
    `html_to_text` breaks text at block-tag boundaries, and financial
    printers routinely wrap one paragraph across several of them. The
    resulting mid-paragraph fragment is short, carries no trailing
    punctuation, and used to satisfy every heading test -- so a
    paragraph could silently become three "sub-themes".

    That is worse than cosmetic: it fragments the diff and, since the
    sub-theme list is what the selection pass chooses from, it spends
    the model's limited picks on fragments of running prose.
    """
    text = (
        "Critical Accounting Estimates\n"
        "Our critical accounting estimates are unchanged from those described\n"
        "in our most recent Annual Report, and require management to make\n"
        "judgments about matters that are inherently uncertain\n"
    )
    from equity_analyzer.diff.grouped_diff import split_into_groups

    assert [h for h, _ in split_into_groups(text)] == ["Critical Accounting Estimates"]


def test_part_dividers_are_not_sub_themes():
    """
    A section ends at the next ITEM header, and a "PART II" divider sits
    just before one -- so it lands inside the extracted text. It is all
    caps, short and unpunctuated, i.e. indistinguishable from a heading
    on every test but this one.
    """
    from equity_analyzer.diff.grouped_diff import split_into_groups

    headings = [h for h, _ in split_into_groups(
        "Results of Operations\nRevenue grew.\n\nPART II\nOther Information\nNone.\n"
    )]
    assert "PART II" not in headings
    assert "Results of Operations" in headings


def test_all_caps_headings_are_still_recognised():
    """Many filers set headings in caps; the title-case test must accept them."""
    from equity_analyzer.diff.grouped_diff import split_into_groups

    headings = [h for h, _ in split_into_groups(
        "RESULTS OF OPERATIONS\nRevenue grew 12%.\n\nLIQUIDITY\nCash was flat.\n"
    )]
    assert headings == ["RESULTS OF OPERATIONS", "LIQUIDITY"]


def test_the_unheaded_opening_block_survives_a_selection():
    """
    The text before a section's first sub-heading is never on the ballot
    the selector is handed (it has no heading to choose), so dropping it
    would not be a decision the selector made. In an MD&A that block
    routinely carries the overview and the guidance.
    """
    from equity_analyzer.diff.grouped_diff import apply_theme_selection

    prior = "We expect margins to hold.\n\nLiquidity\nCash was 4 billion.\n"
    current = "We expect margins to compress.\n\nLiquidity\nCash was 5 billion.\n"
    result = diff_text_grouped(prior, current)
    marked = apply_theme_selection(result, ["Liquidity"])

    by_heading = {g.heading: g for g in marked.groups}
    assert by_heading[""].selected is True
    # ...but behind what was actually chosen
    assert by_heading[""].selection_rank > by_heading["Liquidity"].selection_rank
