"""
One report, as a machine-readable record next to its PDF.

WHY, and it is the whole point of keeping reports at all. The PDF is for
a person: it is laid out, paginated, meant to be read one at a time. A
cross-company or sector view is a different question entirely, asked of
MANY reports at once, and no tool reasons across eight PDFs. So each run
also writes what it produced as JSON, and a later pass can load a dozen
of them and synthesise.

WHAT IT KEEPS, and why it is more than the history store holds. The
history under `historique/` is deliberately minimal (commitments and
dodges only) because it feeds the per-report baseline and must not
change when the report's prose is reworded. This record has the opposite
job: it is a snapshot of one finished report, reading text included, so
that a sector synthesis has the actual narrative to work from and not
just a table of figures. The two stores answer two different questions
and neither replaces the other.

STRUCTURED, NOT PROSE-ONLY. The verdict, the expectations, the dodge
counts are pulled out as fields rather than left buried in the reading,
so a synthesis can filter and group ("show me every company in the
group whose FCF turned negative this quarter") without re-parsing the
paragraph. The reading text is kept too, for the parts no schema
anticipates.
"""

from __future__ import annotations

from typing import Optional


def _verdict_line(reading: str) -> str:
    """
    The one line under "## Verdict", which is the report's headline.

    Pulled out so a sector view can sort or group on it without loading
    the whole reading. Best effort: a reading that does not follow the
    template simply yields an empty verdict rather than raising.
    """
    lines = (reading or "").splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## verdict"):
            for follow in lines[i + 1:]:
                if follow.strip():
                    return follow.strip()
    return ""


def _expectation_record(section) -> Optional[dict]:
    """The consensus figures, or None when there were none."""
    if section is None:
        return None
    value = getattr(section, "value", None)
    if value is None:
        return {"disponible": False,
                "raison": getattr(section, "unavailable_reason", None)}
    return {
        "disponible": True,
        "bpa_attendu": getattr(value, "estimated_eps", None),
        "bpa_publie": getattr(value, "reported_eps", None),
        "surprise_pct": getattr(value, "surprise_pct", None),
        "comparable": getattr(value, "comparable", None),
    }


def _qa_record(qa) -> Optional[dict]:
    """The Q&A pass, counts plus the findings themselves."""
    if qa is None:
        return None
    return {
        "esquives": list(getattr(qa, "dodged_questions", []) or []),
        "esquives_graves": len(getattr(qa, "hard_dodges", []) or []),
        "concessions": list(getattr(qa, "concessions", []) or []),
        "signaux_prospectifs": list(getattr(qa, "implicit_guidance", []) or []),
        "themes_recurrents": list(getattr(qa, "recurring_themes", []) or []),
        "chiffres_a_verifier": list(getattr(qa, "uncertain_figures", []) or []),
    }


def report_record(report) -> dict:
    """
    A JSON-able snapshot of one finished report.

    Everything is read defensively: this runs at the very end of a run
    that already produced its PDF, so a missing field must degrade the
    record, never raise and cost the archive.
    """
    analysis = report.analysis
    reading = getattr(analysis, "text", "") or ""
    return {
        "ticker": report.ticker,
        "societe": report.company_name,
        "trimestre": report.call.quarter,
        "international": bool(getattr(report, "international", False)),
        "genere_le": report.generated_at.date().isoformat(),
        "modele": getattr(analysis, "model", None),
        "verdict": _verdict_line(reading),
        "lecture": reading,
        "attentes": _expectation_record(report.expectations),
        "qa": _qa_record(getattr(report, "qa_analysis", None)),
        "source_transcript": report.call.source,
        "mots_transcript": report.call.word_count,
    }


__all__ = ["report_record"]
