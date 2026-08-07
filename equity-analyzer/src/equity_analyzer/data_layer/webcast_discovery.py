"""
Reconnaissance: for a list of companies, WHERE the earnings call lives
and in WHAT format.

This exists because the honest answer to "can we just build our own
transcript tool" is "the transcription is easy, acquiring the audio is
not". Every issuer hosts its webcast somewhere different. The way out is
not to hardcode a URL per company -- that rots the first time an IR site
is redesigned -- but to identify WHICH VENDOR each company uses. There
are only a handful, each with a stable URL pattern, so a hundred
companies collapse to a handful of adapters.

And rather than guess which vendor each one uses, this module goes and
looks. It produces a survey, not a downloader: run it over a ticker list
and it reports, per company, what is actually reachable. That turns
"should we build this?" from a judgement call into a table. If the
survey comes back saying two thirds of the list is behind a registration
wall, that is the answer, and it cost one run instead of a month.

TWO ROUTES ARE CHECKED PER COMPANY, cheapest first:

  1. EDGAR. Does the filer attach a transcript or prepared remarks to
     its earnings 8-K? Free, already reachable, no crawling. A minority
     do, and for those nothing else is needed.
  2. The IR site. SEC's own submissions endpoint carries the company's
     investor-relations URL, so there is no guessing at the address
     either. The page is fetched and matched against known vendor
     signatures.

THE SIGNATURES BELOW ARE UNVALIDATED. They come from general knowledge
of who the IR webcast vendors are, not from having inspected these
pages -- the environment this was written in cannot reach any of them.
That is precisely why this is a survey: its first run is what turns the
guesses into facts, and a vendor that never matches is a signature to
fix, not a company to give up on. Treat the first output as a draft.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

DEFAULT_TIMEOUT_SECONDS = 20.0
# IR sites are ordinary corporate web servers, not an API with a
# published rate limit. One request per second per host is well inside
# what any of them expects from a browser, and this only ever fetches
# one page per company.
DEFAULT_REQUESTS_PER_SECOND = 1.0


@dataclass(frozen=True)
class VendorSignature:
    """
    How to recognise one webcast host, and what recognising it implies.

    `fetch_route` is the point of the whole exercise: it says what a
    downloader would have to do for this vendor, so the survey answers
    "in what format" and not merely "who".
    """
    vendor: str
    patterns: tuple
    fetch_route: str


# Ordered most-specific first: a page can carry several of these (a Q4
# site embedding a YouTube replay), and the first match is the primary
# host.
VENDOR_SIGNATURES = (
    VendorSignature(
        vendor="Q4 Inc",
        patterns=("q4cdn.com", "q4inc.com", "q4web.com", "s2.q4cdn", "q4-cdn"),
        fetch_route=(
            "Q4 serves event metadata as JSON from the company's own Q4 site; "
            "the replay is usually a direct media URL in that payload"
        ),
    ),
    VendorSignature(
        vendor="Notified / Intrado",
        patterns=("notified.com", "investorcalendar.com", "viavid.com", "issuerdirect.com"),
        fetch_route=(
            "Notified serves a player page per event; the media URL is in the "
            "page, sometimes behind a registration form"
        ),
    ),
    VendorSignature(
        vendor="Nasdaq IR",
        patterns=("nasdaq.com/market-activity", "ir.nasdaq.com", "irsolutions.nasdaq"),
        fetch_route="Nasdaq-hosted player page; media URL embedded in the page",
    ),
    VendorSignature(
        vendor="webcast platform (generic)",
        patterns=("webcasts.com", "webcaster", "onstreammedia", "kaltura", "brightcove"),
        fetch_route="Generic streaming host; expect HLS/DASH rather than a plain file",
    ),
    VendorSignature(
        vendor="YouTube",
        patterns=("youtube.com/embed", "youtu.be/", "youtube-nocookie.com"),
        fetch_route=(
            "Fetchable, but check who uploaded it: a third-party re-upload is "
            "not the company's own publication"
        ),
    ),
    VendorSignature(
        vendor="podcast feed",
        patterns=("/rss", "feed.xml", "podcast", "libsyn", "megaphone.fm"),
        fetch_route=(
            "The best case by far: an RSS enclosure is a plain audio file with "
            "a date, no player to reverse engineer"
        ),
    ),
    VendorSignature(
        vendor="Zoom",
        patterns=("zoom.us/webinar", "zoom.us/rec"),
        fetch_route="Usually registration-gated; often not fetchable unattended",
    ),
)

# A link straight to a media file: the case that needs no adapter at all.
_AUDIO_LINK_RE = re.compile(
    r"""["'(]([^"'()\s]+?\.(?:mp3|m4a|mp4|wav|aac))(?:\?[^"'()\s]*)?["')]""",
    re.IGNORECASE,
)
# A registration/login wall in front of the replay -- the outcome that
# makes a company unfetchable unattended, and worth counting separately
# from "no vendor detected".
_GATE_RE = re.compile(
    r"register\s+to\s+(?:listen|view|watch)|registration\s+required"
    r"|sign\s+in\s+to\s+(?:listen|view)|create\s+an\s+account",
    re.IGNORECASE,
)

_IR_URL_KEYS = ("investorWebsite", "investor_website", "website")


@dataclass(frozen=True)
class TranscriptCoverage:
    """
    What one company offers. Every field is a fact observed or an
    explicit absence -- nothing here is inferred from what a peer does.
    """
    ticker: str
    cik: str
    ir_url: Optional[str] = None
    edgar_transcript_exhibit: Optional[str] = None
    vendors: tuple = ()
    fetch_routes: tuple = ()
    direct_audio_links: tuple = ()
    registration_gated: bool = False
    # Whether the IR page was actually READ. Without this, a company
    # whose address could not be found and one whose page was read and
    # matched nothing both report as "rien trouvé" -- and the first
    # means the survey has a hole in it while the second is a real
    # measurement. The first run of this survey collapsed exactly that
    # way and reported an empty result that looked like a finding.
    ir_page_read: bool = False
    ir_note: Optional[str] = None   # why the page was not read, when it was not
    error: Optional[str] = None

    @property
    def verdict(self) -> str:
        """One word for the summary table, in order of how good the news is."""
        if self.edgar_transcript_exhibit:
            return "EDGAR"          # free text, nothing else needed
        if self.direct_audio_links:
            return "audio direct"   # fetchable with no adapter
        if self.vendors:
            return "gated" if self.registration_gated else "vendeur"
        if self.error:
            return "erreur"
        if not self.ir_page_read:
            # Checked BEFORE "rien trouvé": the survey never got to
            # look, so there is no finding here to report either way.
            return "IR non lue"
        return "rien trouvé"


def investor_relations_url(submissions: dict) -> Optional[str]:
    """
    The company's IR page from SEC's own submissions record, when it is
    there.

    Sourcing the address beats constructing one -- the issuer told the
    SEC where it is. But this field is NOT part of the documented core
    of the endpoint, and the first real run of this survey suggests SEC
    frequently omits it: every company came back with no IR page and no
    error, which is what an absent field looks like. So this returns
    None rather than falling back to a guessed hostname, and the caller
    records that the page was never read instead of reporting an empty
    finding.
    """
    for key in _IR_URL_KEYS:
        value = submissions.get(key)
        if isinstance(value, str) and value.strip():
            value = value.strip()
            return value if value.startswith("http") else f"https://{value}"
    return None


def submissions_shape(submissions: dict) -> str:
    """
    The top-level keys SEC actually returned, for the survey to print.

    The same discipline as the Alpha Vantage check: when a guess about
    someone else's payload turns out wrong, the output should say what
    the right answer is instead of leaving a silent hole.
    """
    return ", ".join(sorted(k for k in submissions if not isinstance(submissions[k], (dict, list))))


def detect_vendors(html: str) -> tuple:
    """
    Which known webcast hosts appear in a page, most specific first, with
    what each implies for fetching.
    """
    lowered = html.lower()
    found = []
    for signature in VENDOR_SIGNATURES:
        if any(pattern in lowered for pattern in signature.patterns):
            found.append(signature)
    return tuple(found)


def find_audio_links(html: str, base_url: Optional[str] = None, limit: int = 5) -> tuple:
    """
    Direct links to media files, absolutised against the page they were
    found on. These are the companies that need no adapter whatsoever,
    so they are worth spotting separately from a vendor match.
    """
    links = []
    for match in _AUDIO_LINK_RE.finditer(html):
        href = match.group(1)
        if base_url and not href.lower().startswith(("http://", "https://")):
            href = urljoin(base_url, href)
        if href not in links:
            links.append(href)
        if len(links) >= limit:
            break
    return tuple(links)


class PageFetcher:
    """
    A throttled, identifiable HTTP getter for ordinary corporate sites.

    Separate from EdgarClient on purpose: that one carries SEC's required
    contact-email User-Agent and SEC's rate limit, neither of which is
    right for someone else's web server.
    """

    def __init__(
        self,
        user_agent: str,
        requests_per_second: float = DEFAULT_REQUESTS_PER_SECOND,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})
        self._min_interval = 1.0 / requests_per_second if requests_per_second else 0.0
        self._timeout = timeout_seconds
        self._last_request = 0.0

    def get(self, url: str) -> str:
        wait = self._min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        response = self._session.get(url, timeout=self._timeout)
        response.raise_for_status()
        return response.text


def survey_ticker(
    ticker: str,
    cik: str,
    *,
    edgar_client,
    page_fetcher=None,
) -> TranscriptCoverage:
    """
    Both routes for one company, cheapest first.

    Never raises. A company whose IR site is down, slow, or hostile to
    robots must not take down a hundred-ticker survey, and "we could not
    reach it" is itself a result worth recording -- it is the difference
    between a company that offers nothing and one this run failed to
    ask.
    """
    from .earnings_release import fetch_earnings_release, list_earnings_8ks

    exhibit_name = None
    errors = []
    ir_note = None

    try:
        refs = list_earnings_8ks(edgar_client, cik, limit=1)
        release = fetch_earnings_release(edgar_client, cik, refs[0])
        if release.transcript is not None:
            exhibit_name = release.transcript.document
    except Exception as exc:  # noqa: BLE001 -- a survey reports failures, it doesn't propagate them
        errors.append(f"EDGAR: {exc}")

    ir_url = None
    vendors = ()
    routes = ()
    audio = ()
    gated = False
    ir_page_read = False
    try:
        submissions = edgar_client.fetch_submissions(cik)
        ir_url = investor_relations_url(submissions)
        if ir_url is None:
            # Kept out of `error`, which is for things that went wrong.
            # A missing address is a gap in the survey's inputs, and it
            # gets its own verdict so it can never be read as a finding
            # about the company.
            ir_note = f"pas d'URL IR dans les données SEC (champs: {submissions_shape(submissions)})"
    except Exception as exc:  # noqa: BLE001
        errors.append(f"submissions: {exc}")

    if ir_url and page_fetcher is not None:
        try:
            html = page_fetcher.get(ir_url)
            ir_page_read = True
            signatures = detect_vendors(html)
            vendors = tuple(s.vendor for s in signatures)
            routes = tuple(s.fetch_route for s in signatures)
            audio = find_audio_links(html, ir_url)
            gated = bool(_GATE_RE.search(html))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"IR page: {exc}")

    return TranscriptCoverage(
        ticker=ticker,
        cik=cik,
        ir_url=ir_url,
        edgar_transcript_exhibit=exhibit_name,
        vendors=vendors,
        fetch_routes=routes,
        direct_audio_links=audio,
        registration_gated=gated,
        ir_page_read=ir_page_read,
        ir_note=ir_note,
        error="; ".join(errors) or None,
    )


__all__ = [
    "PageFetcher",
    "TranscriptCoverage",
    "VENDOR_SIGNATURES",
    "VendorSignature",
    "detect_vendors",
    "find_audio_links",
    "investor_relations_url",
    "survey_ticker",
]
