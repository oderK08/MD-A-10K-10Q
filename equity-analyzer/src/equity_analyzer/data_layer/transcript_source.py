"""
Where a call transcript comes from.

The earnings call is where the information the user is after actually
lives: the prepared remarks are what management chose to say about its
own quarter, and the Q&A is the only place it is asked questions it did
not choose. Neither is on EDGAR for most filers.

This module is the seam. Everything upstream (fetching) varies by
source; everything downstream (the reading, the tone scoring) does not
care where the text came from, as long as it arrives as a
`CallTranscript`. So the pipeline is written against this interface, and
a source is plugged in behind it.

THE FOUR SOURCES, and what each really costs:

  1. EDGAR 8-K EX-99.2 (`EdgarExhibitSource`, implemented here).
     Free, already reachable with the client this project has, no
     account. Covers only the minority of filers who choose to attach a
     transcript or their prepared remarks. Where it works it is the
     genuine article, straight from the company.

  2. A licensed transcript API (`HttpTranscriptSource`, implemented
     here, unvalidated). One account, one key, reliable coverage. The
     adapter below is written against the shape these APIs converge on
     rather than any one vendor's docs, and is configured with the
     field names to read; it has never run against a live endpoint.

  3. Transcribing the call audio yourself. The transcription is the
     easy half: an hour of scripted business English is well within
     what a Whisper-class model handles, for cents. The hard half is
     ACQUIRING the audio -- every issuer hosts its webcast differently
     (Q4 Inc, Notified, Nasdaq, a bare MP3 on the IR site), several put
     it behind a registration form, and some offer only a phone replay
     with a passcode, which cannot be fetched at all. That is an
     adapter per host and a permanent maintenance burden, and coverage
     is worst exactly where it matters most, on small caps. Not
     implemented; the interface is here so it can be.

  4. Scraping a transcript site. Cheapest to write, and the one route
     this module deliberately does not provide: those transcripts are
     copyrighted works and their terms forbid it. Transcribing a public
     call you were invited to listen to (option 3) is a different act
     legally, and is the honest DIY route.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import requests

DEFAULT_TIMEOUT_SECONDS = 30.0

# Keys under which vendors return a refusal alongside HTTP 200: an
# exhausted quota, a premium-only endpoint, an unrecognised symbol. None
# of these is signalled by a status code, so `response.ok` is true for
# all of them.
_SOFT_ERROR_KEYS = ("Information", "Note", "Error Message")

# The operator's handover lines, which mark where prepared remarks stop
# and Q&A starts. Both halves are worth reading and they are worth
# reading DIFFERENTLY: prepared remarks are written, lawyered and
# rehearsed, so their tone is a decision management made, while the Q&A
# is unscripted and its value is in which topics analysts pushed on and
# what they did not get answered.
# Matched anywhere in a line, not anchored at its start: the operator's
# handover is a sentence ("We will now begin the question-and-answer
# session"), and the wording of the surrounding clause varies far more
# than the phrase itself does. Trying to enumerate the openings
# ("we're now", "we will now", "at this time we will") is how a first
# version of this missed the most common phrasing of all.
_QA_BOUNDARY_RE = re.compile(
    r"questions?[\s-]and[\s-]answers?(?:\s+session)?"
    r"|\[\s*q\s*&\s*a\s*\]"
    r"|(?:begin|open|start|take|move to).{0,20}\bq\s*&\s*a\b"
    # The markers that actually appear at the handover on real calls.
    # Microsoft's Q3 2026 transcript named the Q&A only in the operator's
    # OPENING ("a question and answer session will follow"), which is
    # correctly rejected as an announcement -- and the real handover used
    # none of the phrases above, so the split found nothing and filed all
    # 8815 words as prepared remarks. These three are how an operator
    # actually opens the floor.
    r"|\[\s*operator\s+instructions\s*\]"
    r"|(?:first|next)\s+question\s+(?:comes\s+|is\s+)?from"
    r"|press\s+star\s+(?:one|1)\b",
    re.IGNORECASE,
)

# The operator ANNOUNCES the Q&A in the opening boilerplate, long before
# handing over to it: "A question and answer session will follow the
# formal presentation." That sentence matches the boundary pattern
# perfectly, and on the first live Microsoft call it moved the split to
# the second line of the transcript -- three words of prepared remarks
# and the entire call filed as Q&A. IBM's operator happened not to say
# it, so the flaw survived the first check.
#
# The tell is tense: an announcement points forward ("will follow",
# "at the end of"), a handover is happening now ("we will now begin").
# Matching on the forward reference is more precise than trying to
# enumerate every way an operator says "now".
_QA_ANNOUNCEMENT_RE = re.compile(
    r"will\s+(?:follow|be\s+held|be\s+conducted|begin\s+(?:after|following|at\s+the\s+end))"
    r"|(?:at|after|following)\s+the\s+(?:end|conclusion|completion)\s+of"
    r"|following\s+(?:the\s+)?(?:formal\s+)?(?:presentation|prepared\s+remarks)"
    r"|after\s+(?:the\s+)?(?:formal\s+)?(?:presentation|prepared\s+remarks)"
    r"|later\s+in\s+(?:the|this)\s+call",
    re.IGNORECASE,
)

# Second guard, independent of wording: prepared remarks are the bulk of
# an earnings call, never its first line. A boundary found in the
# opening turns is the operator's preamble, whatever it says.
_MIN_PREPARED_SHARE = 0.10


class TranscriptUnavailable(Exception):
    """
    No transcript for this quarter from this source.

    Raised rather than returned as an empty transcript, because "we
    could not get it" and "the call said nothing" must never be
    confusable downstream: page 1 of the report is a reading of the
    call, and it can refuse to run without material only if the absence
    is unambiguous.
    """


class TranscriptRefused(TranscriptUnavailable):
    """
    The provider declined to answer at all: quota exhausted,
    premium-only endpoint, bad key.

    Separate from a plain absence because the two demand opposite
    reactions, and the first live run made that concrete. AAOI's newest
    quarter simply was not published yet, which is worth retrying
    against an older quarter. A refusal means every further request will
    be refused too, so walking back through quarters would burn the rest
    of the day's budget to learn nothing.
    """


@dataclass(frozen=True)
class CallTranscript:
    """
    One earnings call, as text.

    `prepared_remarks` and `qa` are split when the operator's handover
    can be located, because the two are different acts and are scored
    separately (see report/report_data.build_call_report). When it
    cannot be located, `prepared_remarks` holds the whole call and `qa`
    is None, stated rather than silently merged.
    """
    ticker: str
    call_date: Optional[date]
    fiscal_period: Optional[str]
    full_text: str
    prepared_remarks: str
    qa: Optional[str]
    source: str  # how it was obtained, printed in the report for provenance

    @property
    def word_count(self) -> int:
        return len(self.full_text.split())


def split_prepared_from_qa(text: str) -> tuple:
    """
    Returns (prepared_remarks, qa_or_None), split at the operator's
    handover to questions.

    Only the FIRST handover counts. The phrase recurs later ("our next
    question comes from..."), and splitting on a recurrence would put
    most of the Q&A back into the prepared remarks.
    """
    lines = text.split("\n")
    earliest = int(len(lines) * _MIN_PREPARED_SHARE)

    for index, line in enumerate(lines):
        if not _QA_BOUNDARY_RE.search(line):
            continue
        # The operator's opening boilerplate announces the Q&A rather
        # than starting it. Two independent guards, because either alone
        # has a failure mode: the wording test misses an announcement
        # phrased unusually, and the position test would misfire on a
        # call with genuinely short prepared remarks.
        if _QA_ANNOUNCEMENT_RE.search(line):
            continue
        if index < earliest:
            continue
        prepared = "\n".join(lines[:index]).strip()
        qa = "\n".join(lines[index:]).strip()
        if prepared and qa:
            return prepared, qa
        break
    return text.strip(), None


class TranscriptSource:
    """
    The interface the pipeline depends on. One method, so that adding a
    source is writing one class and changing one line of wiring.
    """

    name = "abstract"

    def fetch(self, ticker: str, cik: str, client=None) -> CallTranscript:
        raise NotImplementedError


class EdgarExhibitSource(TranscriptSource):
    """
    The transcript as an 8-K exhibit, when the filer attaches one.

    Free and already reachable. Coverage is the catch: most issuers
    never file one, and for them this raises `TranscriptUnavailable`
    rather than returning the press release instead. Handing back the
    press release under the name "transcript" would be the kind of
    quiet substitution that makes a report untrustworthy.
    """

    name = "EDGAR 8-K exhibit"

    def fetch(self, ticker: str, cik: str, client=None) -> CallTranscript:
        from .earnings_release import fetch_earnings_release, list_earnings_8ks
        from .earnings_text import extract_earnings_text

        if client is None:
            raise TranscriptUnavailable("no EDGAR client supplied")

        refs = list_earnings_8ks(client, cik, limit=1)
        release = fetch_earnings_release(client, cik, refs[0])
        if release.transcript is None:
            raise TranscriptUnavailable(
                f"{ticker} did not attach a transcript to its earnings 8-K "
                f"(most issuers never do); only the press release is on EDGAR"
            )

        html = client.fetch_filing_document(
            cik, release.ref.accession_number, release.transcript.document
        )
        text = extract_earnings_text(html).narrative
        prepared, qa = split_prepared_from_qa(text)
        return CallTranscript(
            ticker=ticker,
            call_date=release.ref.period_of_report or release.ref.filed_date,
            fiscal_period=None,
            full_text=text,
            prepared_remarks=prepared,
            qa=qa,
            source=f"{self.name} ({release.transcript.document})",
        )


@dataclass
class HttpTranscriptSource(TranscriptSource):
    """
    A licensed transcript API.

    NEVER RUN AGAINST A LIVE ENDPOINT. The vendors' own sites are
    unreachable from the environment this was written in, so the request
    shape and the response field names are configuration rather than
    hardcoded guesses: point `url_template` and the field names at
    whatever the vendor actually returns and this works without a code
    change. Expect to adjust them on the first real call.

    Deliberately no vendor default. A wrong default that happens to
    return HTTP 200 with an unexpected body is worse than no default at
    all, because it fails silently.
    """

    url_template: str          # e.g. "https://host/transcript?symbol={ticker}"
    api_key_env: str = "TRANSCRIPT_API_KEY"
    # Vendors split roughly evenly between a header and a query
    # parameter. Set exactly one: `api_key_param` wins when both are set.
    api_key_header: str = "X-Api-Key"
    api_key_param: Optional[str] = None
    text_field: str = "transcript"
    date_field: str = "date"
    period_field: str = "quarter"
    # When the text field holds a LIST of speaker turns rather than one
    # string -- the better shape, and the one Alpha Vantage uses.
    speaker_field: Optional[str] = None
    content_field: str = "content"
    title_field: Optional[str] = None
    name: str = "transcript API"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    extra_params: dict = field(default_factory=dict)

    def fetch(self, ticker: str, cik: str, client=None, quarter: Optional[str] = None) -> CallTranscript:
        api_key = os.environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise TranscriptUnavailable(
                f"no transcript API key in ${self.api_key_env}"
            )
        url = self.url_template.format(ticker=ticker, cik=cik, quarter=quarter or "")
        params = dict(self.extra_params)
        headers = {}
        if self.api_key_param:
            params[self.api_key_param] = api_key
        else:
            headers[self.api_key_header] = api_key
        try:
            response = requests.get(
                url,
                headers=headers or None,
                params=params or None,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise TranscriptUnavailable(f"network error calling {self.name}: {exc}") from exc

        if response.status_code != 200:
            raise TranscriptUnavailable(
                f"{self.name} returned HTTP {response.status_code}: {response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise TranscriptUnavailable(f"{self.name} returned non-JSON: {exc}") from exc

        # A refusal dressed as a success. Alpha Vantage -- and it is not
        # alone -- answers an exhausted quota, a premium-only endpoint
        # and an unknown symbol with HTTP 200 and a prose message under
        # "Information" or "Note", carrying no data. Checked BEFORE
        # looking for the transcript field, because otherwise all three
        # surface as "no usable 'transcript'", which points the reader
        # at the field names when the real problem is the quota. That
        # exact confusion cost a debugging round on the first live run.
        for key in _SOFT_ERROR_KEYS:
            message = payload.get(key) if isinstance(payload, dict) else None
            if message:
                raise TranscriptRefused(f"{self.name} a refusé ({key}) : {str(message)[:300]}")

        # Vendors return either the object or a one-element list of them.
        if isinstance(payload, list):
            if not payload:
                raise TranscriptUnavailable(f"{self.name} returned no transcript for {ticker}")
            payload = payload[0]
        if not isinstance(payload, dict):
            raise TranscriptUnavailable(f"unexpected {self.name} response shape")

        raw = payload.get(self.text_field)
        text = (_join_turns(raw, self) if isinstance(raw, list) else (raw or "")).strip()
        if not text:
            raise TranscriptUnavailable(
                f"{self.name} response has no usable '{self.text_field}' for {ticker}"
            )

        prepared, qa = split_prepared_from_qa(text)
        return CallTranscript(
            ticker=ticker,
            call_date=_parse_date(payload.get(self.date_field)),
            fiscal_period=payload.get(self.period_field),
            full_text=text,
            prepared_remarks=prepared,
            qa=qa,
            source=self.name,
        )


def _join_turns(turns: list, config) -> str:
    """
    Flattens a list of speaker turns into text, keeping the speaker on
    its own line.

    A turn list is the BETTER shape to receive, not a complication to
    flatten away. Attribution is load-bearing for the reading: "we
    expect prices to increase 5%" said by the CEO in prepared remarks
    and the same sentence said by an analyst framing a question are
    opposite facts, and a flattened wall of text cannot tell them
    apart. Keeping the speaker on its own line is also what lets the
    prepared/Q&A split and the model both read the call the way a
    listener heard it.
    """
    lines = []
    for turn in turns:
        if not isinstance(turn, dict):
            if turn:
                lines.append(str(turn))
            continue
        speaker = str(turn.get(config.speaker_field) or "").strip() if config.speaker_field else ""
        title = str(turn.get(config.title_field) or "").strip() if config.title_field else ""
        header = " -- ".join(part for part in (speaker, title) if part)
        content = str(turn.get(config.content_field) or "").strip()
        if not content:
            continue
        if header:
            lines.append(header)
        lines.append(content)
    return "\n".join(lines)


def alpha_vantage_source(api_key_env: str = "ALPHAVANTAGE_API_KEY") -> HttpTranscriptSource:
    """
    Alpha Vantage's EARNINGS_CALL_TRANSCRIPT endpoint, preconfigured.

    Worth trying BEFORE any of the harder routes. It is a real API, not
    a scrape, so no terms are being violated and nothing breaks when a
    site is redesigned; it returns the call already split by speaker;
    and its free tier is documented at 25 requests a day, which is far
    more than a person analysing a handful of names a week will use. If
    this works, the whole audio-acquisition problem -- the one genuinely
    hard part of a DIY transcript pipeline -- simply does not arise.

    UNVERIFIED, in two ways that matter. The vendor's site is
    unreachable from the environment this was written in, so the URL
    shape and field names come from documentation read second-hand;
    and, more importantly, Alpha Vantage has moved several of its
    "Alpha Intelligence" endpoints to premium, so whether transcripts
    are in the free tier at all is exactly the thing to check first with
    a free key. Treat a working call as the confirmation, not this
    docstring.
    """
    return HttpTranscriptSource(
        url_template=(
            "https://www.alphavantage.co/query"
            "?function=EARNINGS_CALL_TRANSCRIPT&symbol={ticker}&quarter={quarter}"
        ),
        api_key_env=api_key_env,
        api_key_param="apikey",   # Alpha Vantage authenticates by query string, not header
        text_field="transcript",  # a list of speaker turns
        speaker_field="speaker",
        title_field="title",
        content_field="content",
        date_field="quarter",
        period_field="quarter",
        name="Alpha Vantage",
    )


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


__all__ = [
    "CallTranscript",
    "EdgarExhibitSource",
    "alpha_vantage_source",
    "HttpTranscriptSource",
    "TranscriptSource",
    "TranscriptUnavailable",
    "split_prepared_from_qa",
]
