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
    assert any(seg.kind == "added" for seg in demand_group.diff.segments)

    regulation_group = next(g for g in result.groups if g.heading == "Risks Related to Regulation")
    assert regulation_group.status == "matched"
    assert not any(seg.kind in ("added", "removed") for seg in regulation_group.diff.segments)


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
    assert not any(seg.kind in ("added", "removed") for seg in manufacturing_groups[0].diff.segments)

    # the real detailed occurrence (rewritten -- "export controls" is new)
    assert manufacturing_groups[1].status == "matched"
    assert any(seg.kind == "added" for seg in manufacturing_groups[1].diff.segments)
