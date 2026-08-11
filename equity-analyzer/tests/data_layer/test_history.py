"""
The store that turns a snapshot into a series.

WHAT MATTERS HERE. A record is an extra: it makes the next run cheaper
and richer, and it must never be able to cost a report. So the whole
read and write path degrades rather than raises, and most of what
follows checks that a damaged file, a read only disk or a hand edited
filename lands as "not known yet".
"""

from __future__ import annotations

import json

from equity_analyzer.data_layer.history import HistoryStore, QuarterRecord

COMMITMENTS = [{"metric": "capex", "value": "80 milliards", "period": "2025",
                "verbatim": "capex of $80 billion"}]
DODGES = [{"analyst": "Jane Doe", "question": "concentration client",
           "what_was_asked": "le pourcentage exact", "severity": "high"}]


def _record(quarter, commitments=None, dodges=None, ticker="MSFT"):
    return QuarterRecord(
        ticker=ticker, quarter=quarter,
        commitments=commitments if commitments is not None else COMMITMENTS,
        dodges=dodges if dodges is not None else DODGES,
        model="claude-sonnet-5",
    )


def test_a_record_survives_the_round_trip(tmp_path):
    store = HistoryStore(tmp_path)
    store.put(_record("2026Q3"))

    back = store.get("MSFT", "2026Q3")
    assert back.commitments == COMMITMENTS
    assert back.dodges == DODGES
    assert back.model == "claude-sonnet-5"


def test_the_ticker_is_case_insensitive(tmp_path):
    store = HistoryStore(tmp_path)
    store.put(_record("2026Q3", ticker="msft"))
    assert store.get("MSFT", "2026Q3") is not None


def test_an_unknown_quarter_is_none_rather_than_an_error(tmp_path):
    assert HistoryStore(tmp_path).get("MSFT", "2026Q3") is None


def test_a_damaged_record_reads_as_not_known_yet(tmp_path):
    """
    An interrupted run can leave a truncated file. Failing a report over
    it would be the tail wagging the dog: this is an optimisation and an
    extra, so it degrades to "we do not know that quarter".
    """
    store = HistoryStore(tmp_path)
    path = tmp_path / "MSFT" / "2026Q3.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"ticker": "MSFT", "commitm')

    assert store.get("MSFT", "2026Q3") is None


def test_a_record_that_is_not_an_object_reads_as_not_known_yet(tmp_path):
    store = HistoryStore(tmp_path)
    path = tmp_path / "MSFT" / "2026Q3.json"
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2, 3]")

    assert store.get("MSFT", "2026Q3") is None


def test_writing_is_not_allowed_to_raise_on_a_read_only_disk(tmp_path, monkeypatch):
    """
    Same promise as reading, from the other side. A full disk must cost
    the history, never the PDF the run was about to write.
    """
    store = HistoryStore(tmp_path)

    def _boom(*a, **k):
        raise OSError("read-only file system")

    monkeypatch.setattr("pathlib.Path.mkdir", _boom)
    assert store.put(_record("2026Q3")) is None


def test_an_empty_record_is_not_written_at_all(tmp_path):
    """
    A file with two empty lists is indistinguishable from a quarter
    nobody has looked at yet, and it would suppress a later real write.
    """
    store = HistoryStore(tmp_path)
    assert store.put(QuarterRecord("MSFT", "2026Q3", [], [])) is None
    assert not (tmp_path / "MSFT").exists()


# -- The two halves of a quarter arrive on different runs ---------------


def test_a_second_write_fills_the_gaps_instead_of_erasing(tmp_path):
    """
    THE reason writes merge. A quarter is written twice, and by design:
    the run that READS it knows its dodges, and the run one quarter
    later knows its commitments, because a run reading Q4 extracts Q3's
    baseline. A plain overwrite would make each run destroy what the
    other learned, and the series would never contain both.
    """
    store = HistoryStore(tmp_path)
    store.put(QuarterRecord("MSFT", "2026Q3", commitments=[], dodges=DODGES))
    store.put(QuarterRecord("MSFT", "2026Q3", commitments=COMMITMENTS, dodges=[]))

    back = store.get("MSFT", "2026Q3")
    assert back.commitments == COMMITMENTS
    assert back.dodges == DODGES


def test_re_running_a_quarter_repairs_its_record(tmp_path):
    """
    The only case where both sides hold something, and the fresh
    extraction wins. That makes re-running the way to fix a bad record,
    rather than requiring someone to find and delete a file first.

    The dodges the first run learned survive it, which is the point of
    merging rather than overwriting.
    """
    store = HistoryStore(tmp_path)
    store.put(_record("2026Q3"))
    store.put(QuarterRecord("MSFT", "2026Q3",
                            commitments=[{"metric": "autre", "value": "1"}],
                            dodges=[]))

    back = store.get("MSFT", "2026Q3")
    assert back.commitments == [{"metric": "autre", "value": "1"}]
    assert back.dodges == DODGES


# -- Reading the series -------------------------------------------------


def test_history_before_returns_earlier_quarters_newest_first(tmp_path):
    store = HistoryStore(tmp_path)
    for quarter in ("2026Q1", "2026Q2", "2026Q3", "2026Q4"):
        store.put(_record(quarter))

    labels = [r.quarter for r in store.history_before("MSFT", "2026Q4")]
    assert labels == ["2026Q3", "2026Q2", "2026Q1"]


def test_the_quarter_being_read_is_never_in_its_own_history(tmp_path):
    """
    The quarter under judgement cannot be part of the evidence: it would
    let the report check the company's promises against the very call
    that made them.
    """
    store = HistoryStore(tmp_path)
    store.put(_record("2026Q4"))
    store.put(_record("2026Q3"))

    labels = [r.quarter for r in store.history_before("MSFT", "2026Q4")]
    assert labels == ["2026Q3"]


def test_a_year_rollover_orders_chronologically_not_alphabetically(tmp_path):
    """
    Sorted on (year, index) rather than on the string, because "2025Q4"
    sorts after "2026Q1" as text and the series would come back
    scrambled exactly at the year boundary.
    """
    store = HistoryStore(tmp_path)
    for quarter in ("2025Q3", "2025Q4", "2026Q1"):
        store.put(_record(quarter))

    labels = [r.quarter for r in store.history_before("MSFT", "2026Q2")]
    assert labels == ["2026Q1", "2025Q4", "2025Q3"]


def test_the_series_is_bounded(tmp_path):
    store = HistoryStore(tmp_path)
    for year in (2024, 2025, 2026):
        for index in (1, 2, 3, 4):
            store.put(_record(f"{year}Q{index}"))

    assert len(store.history_before("MSFT", "2026Q4", limit=4)) == 4


def test_an_unparsable_filename_cannot_break_the_series(tmp_path):
    """
    Records are meant to be readable and editable on github.com, so a
    hand made file with an odd name will happen. It must not raise in
    the middle of a run.
    """
    store = HistoryStore(tmp_path)
    store.put(_record("2026Q3"))
    (tmp_path / "MSFT" / "notes.json").write_text(json.dumps({"ticker": "MSFT"}))

    assert [r.quarter for r in store.history_before("MSFT", "2026Q4")] == ["2026Q3"]


def test_an_unknown_ticker_has_no_history(tmp_path):
    assert HistoryStore(tmp_path).history_before("NVDA", "2026Q4") == []
