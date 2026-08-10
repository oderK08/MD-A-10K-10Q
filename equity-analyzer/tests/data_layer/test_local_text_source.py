"""
A transcript committed to the repository, as plain text.

The route exists for the run where the provider has nothing and the
person has only a browser: the cache already accepts a hand made entry,
but only in its own JSON shape, which in practice means running a
script, which assumes a terminal.
"""

from __future__ import annotations

import pytest

from equity_analyzer.data_layer.local_text_source import (
    MIN_WORDS,
    LocalTextSource,
    expected_filename,
)
from equity_analyzer.data_layer.transcript_source import TranscriptUnavailable


def _call_text(words=2000):
    body = ["Operator", "Welcome to the fourth quarter earnings call."]
    body += [f"mot{i} revenue margin guidance bookings." for i in range(words // 5)]
    body.append("Our first question comes from Jane Doe with Some Bank.")
    body += [f"reponse{i} sur la marge et le capex." for i in range(200)]
    return "\n".join(body)


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text)
    return LocalTextSource(tmp_path)


def test_the_filename_carries_the_ticker_and_the_fiscal_quarter():
    assert expected_filename("uber", "2026Q2") == "UBER_2026Q2.txt"


def test_a_committed_file_is_read_as_a_transcript(tmp_path):
    source = _write(tmp_path, "UBER_2026Q2.txt", _call_text())
    call = source.fetch("UBER", "0001543151", quarter="2026Q2")

    assert call.ticker == "UBER"
    assert call.fiscal_period == "2026Q2"
    assert call.word_count > MIN_WORDS
    assert "UBER_2026Q2.txt" in call.source


def test_it_is_never_treated_as_a_verbatim_record(tmp_path):
    """
    Whatever arrives this way is overwhelmingly a machine transcription,
    and the expensive mistake is the one that lets a machine's guess
    wear the authority of the company's own written record.
    """
    source = _write(tmp_path, "UBER_2026Q2.txt", _call_text())
    assert source.fetch("UBER", "cik", quarter="2026Q2").verbatim is False


def test_the_prepared_remarks_and_the_qa_are_split_like_any_other_source(tmp_path):
    """
    Same code as every other route, so page 2 scores the two halves of a
    committed call exactly as it scores a provider one.
    """
    source = _write(tmp_path, "UBER_2026Q2.txt", _call_text())
    call = source.fetch("UBER", "cik", quarter="2026Q2")

    assert call.qa is not None
    assert len(call.prepared_remarks.split()) < call.word_count


def test_the_file_is_named_after_the_quarter_so_it_expires_by_itself(tmp_path):
    """
    THE reason the quarter is in the name. A file called UBER.txt would
    quietly override every future run with a stale call, which is the
    kind of failure nobody goes looking for.
    """
    source = _write(tmp_path, "UBER_2026Q2.txt", _call_text())

    with pytest.raises(TranscriptUnavailable):
        source.fetch("UBER", "cik", quarter="2026Q3")


def test_a_truncated_paste_is_refused_loudly(tmp_path):
    """
    A half pasted file would otherwise produce a confident reading of a
    fragment, which is worse than no reading at all.
    """
    source = _write(tmp_path, "UBER_2026Q2.txt", "quelques mots seulement")

    with pytest.raises(TranscriptUnavailable) as exc:
        source.fetch("UBER", "cik", quarter="2026Q2")
    assert "tronqué" in str(exc.value)


def test_no_file_is_a_plain_absence(tmp_path):
    with pytest.raises(TranscriptUnavailable):
        LocalTextSource(tmp_path).fetch("UBER", "cik", quarter="2026Q2")


# -- The chain: local first, provider second ----------------------------


# Plain stubs, deliberately NOT subclasses of LocalTextSource: it is a
# dataclass, so its generated __init__ sets `name` on the instance and a
# class attribute on a subclass never takes effect.


class _Never:
    name = "source vide"

    def fetch(self, *a, **k):
        raise TranscriptUnavailable("rien ici")


class _Refuses:
    name = "fournisseur"

    def fetch(self, *a, **k):
        from equity_analyzer.data_layer.transcript_source import TranscriptRefused
        raise TranscriptRefused("quota epuise")


class _Answers:
    name = "fournisseur"

    def fetch(self, ticker, cik, client=None, quarter=None):
        from equity_analyzer.data_layer.transcript_source import CallTranscript
        return CallTranscript(
            ticker=ticker, call_date=None, fiscal_period=quarter,
            full_text="a b c", prepared_remarks="a b c", qa=None, source=self.name,
        )


def test_the_committed_file_is_used_before_the_provider(tmp_path):
    """
    It is free and it is there because someone put it there on purpose,
    so trying it first costs nothing and saves a request.
    """
    from equity_analyzer.data_layer.transcript_source import ChainedSource

    (tmp_path / "UBER_2026Q2.txt").write_text(_call_text())
    chain = ChainedSource([LocalTextSource(tmp_path), _Answers()])

    assert "UBER_2026Q2.txt" in chain.fetch("UBER", "cik", quarter="2026Q2").source


def test_the_provider_is_used_when_no_file_was_committed(tmp_path):
    from equity_analyzer.data_layer.transcript_source import ChainedSource

    chain = ChainedSource([LocalTextSource(tmp_path), _Answers()])
    assert chain.fetch("UBER", "cik", quarter="2026Q2").source == "fournisseur"


def test_a_refusal_survives_the_chain_as_a_refusal(tmp_path):
    """
    Quota exhaustion means every later request fails too, and the caller
    uses that to stop walking back through quarters instead of burning
    the day's budget on four more copies of the same error. Flattening
    it into a plain absence would throw that signal away.
    """
    from equity_analyzer.data_layer.transcript_source import ChainedSource, TranscriptRefused

    chain = ChainedSource([_Never(), _Refuses()])
    with pytest.raises(TranscriptRefused):
        chain.fetch("UBER", "cik", quarter="2026Q2")


def test_every_reason_reaches_the_message_when_nothing_answers(tmp_path):
    from equity_analyzer.data_layer.transcript_source import ChainedSource

    chain = ChainedSource([_Never(), _Never()])
    with pytest.raises(TranscriptUnavailable) as exc:
        chain.fetch("UBER", "cik", quarter="2026Q2")
    assert "rien ici" in str(exc.value)
