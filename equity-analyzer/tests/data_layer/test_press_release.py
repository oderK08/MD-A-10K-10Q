"""
The earnings press release, and the pairing that decides whether it is
usable at all.

WHAT THIS IS PROTECTING. The Q&A pass is told that the most useful
thing it can find is what carried forward looking value and was NOT in
the press release. It had never seen a press release, so that section
rested on an assumption about an unread document.

Supplying one introduces a worse failure than the one it fixes, and
most of what follows guards against it: attaching the WRONG quarter's
release would make both passes report as new information everything the
real release already contained, confidently and invisibly.
"""

from __future__ import annotations

from datetime import date

import pytest

from equity_analyzer.data_layer.press_release import (
    MIN_WORDS,
    PressRelease,
    PressReleaseUnavailable,
    as_prompt_block,
    fetch_press_release,
)

CIK = "0001158114"


def _release_html(headline, words=600):
    """A press release whose opening names a quarter, then runs on."""
    body = " ".join(f"mot{i}" for i in range(words))
    return f"<html><body><p>{headline}</p><p>{body}</p></body></html>"


class FakeClient:
    """EDGAR, reduced to the three calls this module makes."""

    def __init__(self, rows, indexes, documents):
        self._rows = rows
        self._indexes = indexes
        self._documents = documents
        self.fetched = []

    def fetch_submissions(self, cik):
        return {"filings": {"recent": {
            "form": [r[0] for r in self._rows],
            "items": [r[1] for r in self._rows],
            "accessionNumber": [r[2] for r in self._rows],
            "filingDate": [r[3] for r in self._rows],
            "reportDate": [r[4] for r in self._rows],
            "primaryDocument": [f"{r[2]}.htm" for r in self._rows],
        }}}

    def fetch_filing_index(self, cik, accession_number):
        return self._indexes[accession_number]

    def fetch_filing_document(self, cik, accession_number, document):
        self.fetched.append((accession_number, document))
        return self._documents[(accession_number, document)]


def _index(*items):
    return {"directory": {"item": list(items)}}


def _doc(name, type_, description=""):
    return {"name": name, "type": type_, "description": description}


def _client(*filings):
    """filings: (accession, filed, headline_or_None, words)."""
    rows, indexes, documents = [], {}, {}
    for accession, filed, headline, words in filings:
        rows.append(("8-K", "2.02", accession, filed, filed))
        if headline is None:
            indexes[accession] = _index(_doc(f"{accession}.htm", "8-K"))
            continue
        name = f"{accession}-ex991.htm"
        indexes[accession] = _index(
            _doc(f"{accession}.htm", "8-K"),
            _doc(name, "EX-99.1", "Press Release"),
        )
        documents[(accession, name)] = _release_html(headline, words)
    return FakeClient(rows, indexes, documents)


# -- Pairing -----------------------------------------------------------


def test_the_matching_release_is_returned_with_its_provenance():
    client = _client(
        ("0001-26-000001", "2026-07-29",
         "Microsoft Announces Fourth Quarter Fiscal 2026 Results", 600),
    )
    release = fetch_press_release(client, CIK, "2026Q4")

    assert release.quarter == "2026Q4"
    assert release.word_count > MIN_WORDS
    assert release.accession_number == "0001-26-000001"
    assert release.document.endswith("ex991.htm")


def test_a_release_announcing_another_quarter_is_refused_not_used():
    """
    THE failure this module exists to prevent. A release from the wrong
    quarter would make both passes treat everything the real release
    contained as information that only came out on the call.
    """
    client = _client(
        ("0001-26-000001", "2026-04-29",
         "Microsoft Announces Third Quarter Fiscal 2026 Results", 600),
    )
    with pytest.raises(PressReleaseUnavailable) as exc:
        fetch_press_release(client, CIK, "2026Q4")

    assert "2026Q3" in str(exc.value)
    assert "2026Q4" in str(exc.value)


def test_the_search_steps_to_the_next_filing_when_the_newest_is_the_wrong_quarter():
    """
    The newest earnings 8-K is not always the call being read: the
    provider can publish a call late, and the report then falls back a
    quarter. Taking filings[0] and hoping would mispair exactly there.
    """
    client = _client(
        ("0001-26-000002", "2026-10-28",
         "Microsoft Announces First Quarter Fiscal 2027 Results", 600),
        ("0001-26-000001", "2026-07-29",
         "Microsoft Announces Fourth Quarter Fiscal 2026 Results", 600),
    )
    release = fetch_press_release(client, CIK, "2026Q4")

    assert release.accession_number == "0001-26-000001"
    # It really did open the newer one first and reject it on content.
    assert len(client.fetched) == 2


def test_a_filing_with_no_press_release_exhibit_is_skipped_with_its_reason():
    client = _client(
        ("0001-26-000002", "2026-10-28", None, 0),
        ("0001-26-000001", "2026-07-29",
         "Microsoft Announces Fourth Quarter Fiscal 2026 Results", 600),
    )
    release = fetch_press_release(client, CIK, "2026Q4")
    assert release.accession_number == "0001-26-000001"


def test_a_document_too_short_to_be_a_release_is_refused():
    """
    A few hundred words means a cover page, an XBRL sidecar that slipped
    the classifier, or a document whose tables were stripped to nothing.
    Feeding that in as "what was already public" would make the model
    treat the entire quarter as new information.
    """
    client = _client(
        ("0001-26-000001", "2026-07-29",
         "Microsoft Announces Fourth Quarter Fiscal 2026 Results", 20),
    )
    with pytest.raises(PressReleaseUnavailable) as exc:
        fetch_press_release(client, CIK, "2026Q4")
    assert "mots seulement" in str(exc.value)


def test_a_release_that_never_names_a_quarter_is_refused():
    """
    Unnamed is not the same as matching. Accepting it would be assuming
    the pairing, which is the whole thing this avoids.
    """
    client = _client(
        ("0001-26-000001", "2026-07-29", "Microsoft Announces Results", 600),
    )
    with pytest.raises(PressReleaseUnavailable) as exc:
        fetch_press_release(client, CIK, "2026Q4")
    assert "trimestre non nommé" in str(exc.value)


def test_no_earnings_filing_at_all_says_so():
    client = FakeClient([], {}, {})
    with pytest.raises(PressReleaseUnavailable):
        fetch_press_release(client, CIK, "2026Q4")


def test_the_fiscal_label_wins_over_the_calendar():
    """
    Microsoft's fiscal Q4 2026 ends in June 2026. A calendar reading of
    that date would call it Q2, and the release says "Fourth Quarter
    Fiscal 2026" because that is what the company calls it. The pairing
    follows the company, like everything else in this pipeline.
    """
    client = _client(
        ("0001-26-000001", "2026-07-29",
         "Microsoft Announces Fourth Quarter Fiscal 2026 Results", 600),
    )
    assert fetch_press_release(client, CIK, "2026Q4").quarter == "2026Q4"


# -- How the passes see it ---------------------------------------------


def test_the_block_frames_the_release_as_a_yardstick_not_as_material():
    """
    Without this the model reports ON the release, spending words of a
    hard capped page on a document the reader already has.
    """
    block = as_prompt_block(PressRelease(
        quarter="2026Q4", text="le communiqué", document="ex991.htm",
        accession_number="0001", filed_date=date(2026, 7, 29),
    ))

    assert "le communiqué" in block
    assert "DEJA PUBLIC" in block
    assert "Tu ne l'analyses pas" in block


def test_a_missing_release_is_stated_loudly_rather_than_left_out():
    """
    The failure mode supplying a release could introduce. A model asked
    what was NOT in the release, and handed none, does not conclude it
    cannot tell: it reconstructs a plausible release from what such
    documents usually contain and answers against that, and the answer
    reads exactly like one checked against the real thing.
    """
    block = as_prompt_block(None, reason="non apparié à 2026Q4")

    assert "NON DISPONIBLE" in block
    assert "non apparié à 2026Q4" in block
    assert "N'affirme jamais qu'une information ne figurait pas" in block


def test_neither_block_contains_an_em_dash():
    release = PressRelease(quarter="2026Q4", text="x", document="d",
                           accession_number="a")
    for block in (as_prompt_block(release), as_prompt_block(None)):
        assert "—" not in block and "–" not in block
