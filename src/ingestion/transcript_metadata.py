"""
Extract structured metadata from Rockwool FactSet transcript PDFs.

Source of truth is always the PDF content — the FactSet header on every page contains:
  ROCKWOOL A/S (ROCK.B.DK)
  Q4 2024 Earnings Call          ← event type + quarter + reporting year
  Corrected Transcript
  07-Feb-2025                    ← authoritative call date

Filename is used only as a fallback when content parsing fails.
"""

import re
from datetime import date
from pathlib import Path
from typing import Optional

import pdfplumber

from src.models.schema import EventType, TranscriptMetadata

# ── Content-based patterns (from PDF text) ──────────────────────────────────

# FactSet date line: "07-Feb-2025", "20-May-2021", "31-Aug-2022"
_CONTENT_DATE_RE = re.compile(
    r"\b(\d{1,2})[-\s](Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)[-\s](\d{4})\b",
    re.IGNORECASE,
)

# Call title line: "Q4 2024 Earnings Call", "Q1 2021 Earnings Call"
_CALL_TITLE_RE = re.compile(
    r"\b(Q[1-4])\s+(20\d{2})\s+(Earnings Call|Results)",
    re.IGNORECASE,
)

# Event type markers in content
_CONTENT_EVENT_MARKERS = {
    "extraordinary": EventType.EXTRAORDINARY_MEETING,
    "annual general meeting": EventType.AGM,
    "esg": EventType.ESG_MEETING,
    "analyst meeting": EventType.ANALYST_MEETING,
    "earnings call": EventType.EARNINGS_CALL,
}

# ── Filename fallback patterns ───────────────────────────────────────────────

_TRAILING_DATE_RE = re.compile(r"-(\d{4})-(\d{2})-(\d{2})-\d+\.pdf$", re.IGNORECASE)
_INLINE_DATE_RE = re.compile(
    r"\b(\d{1,2})(January|February|March|April|May|June|July|"
    r"August|September|October|November|December)(\d{4})\b",
    re.IGNORECASE,
)
_QUARTER_RE = re.compile(r"\b(Q[1-4])\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(20[12]\d)\b")

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}


def _read_header_text(pdf_path: Path, max_pages: int = 3) -> str:
    """Extract text from the first N pages of the PDF — enough to find the FactSet header."""
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:max_pages]:
            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
            parts.append(text)
    return "\n".join(parts)


def _parse_date_from_content(text: str) -> Optional[date]:
    """Find the FactSet date line (e.g. '07-Feb-2025') in the header text."""
    for m in _CONTENT_DATE_RE.finditer(text):
        day = int(m.group(1))
        month_str = m.group(2)[:3].lower()
        year = int(m.group(3))
        month = _MONTH_MAP.get(month_str)
        if month and 2015 <= year <= 2030 and 1 <= day <= 31:
            try:
                return date(year, month, day)
            except ValueError:
                continue
    return None


def _parse_event_type_from_content(text: str) -> EventType:
    # Only check the first 15 lines — the FactSet title always appears in the header.
    # Searching the full text causes false positives (e.g. "ESG" mentioned in earnings call body).
    title_lines = "\n".join(text.splitlines()[:15]).lower()
    for marker, etype in _CONTENT_EVENT_MARKERS.items():
        if marker in title_lines:
            return etype
    return EventType.UNKNOWN


def _parse_quarter_from_content(text: str) -> Optional[str]:
    m = _CALL_TITLE_RE.search(text)
    return m.group(1).upper() if m else None


def _parse_reporting_year_from_content(text: str, call_date: date) -> int:
    """
    Reporting year differs from call date for Q4 earnings calls.
    e.g. 'Q4 2024 Earnings Call' held on 07-Feb-2025 → reporting year = 2024.
    """
    m = _CALL_TITLE_RE.search(text)
    if m:
        return int(m.group(2))
    return call_date.year


# ── Filename fallbacks ───────────────────────────────────────────────────────

def _date_from_filename(filename: str) -> Optional[date]:
    m = _TRAILING_DATE_RE.search(filename)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _INLINE_DATE_RE.search(filename)
    if m:
        month = _MONTH_MAP.get(m.group(2).lower())
        if month:
            return date(int(m.group(3)), month, int(m.group(1)))
    years = _YEAR_RE.findall(filename)
    if years:
        return date(int(years[-1]), 1, 1)
    return None


def _event_type_from_filename(filename: str) -> EventType:
    fn = filename.upper()
    if "EXTRAORDINARY" in fn:
        return EventType.EXTRAORDINARY_MEETING
    if "ANNUAL GENERAL" in fn:
        return EventType.AGM
    if "ESG" in fn:
        return EventType.ESG_MEETING
    if "ANALYST" in fn:
        return EventType.ANALYST_MEETING
    if "EARNINGS" in fn:
        return EventType.EARNINGS_CALL
    return EventType.UNKNOWN


def _quarter_from_filename(filename: str) -> Optional[str]:
    m = _QUARTER_RE.search(filename)
    return m.group(1).upper() if m else None


def _reporting_year_from_filename(filename: str, call_date: date) -> int:
    years = _YEAR_RE.findall(filename)
    if len(years) >= 2:
        return int(years[0])
    if years:
        return int(years[0])
    return call_date.year


# ── Public API ───────────────────────────────────────────────────────────────

def extract_metadata(pdf_path: str | Path) -> TranscriptMetadata:
    """
    Extract transcript metadata, preferring PDF content over filename.
    Falls back to filename parsing if content parsing yields no result.
    """
    path = Path(pdf_path)
    filename = path.name

    header_text = _read_header_text(path)

    # Date — content first, filename fallback
    transcript_date = _parse_date_from_content(header_text)
    if transcript_date is None:
        transcript_date = _date_from_filename(filename)
    if transcript_date is None:
        raise ValueError(f"Cannot determine date for: {filename}")

    # Event type — content first
    event_type = _parse_event_type_from_content(header_text)
    if event_type == EventType.UNKNOWN:
        event_type = _event_type_from_filename(filename)

    # Quarter — content first (only present for earnings calls)
    quarter = _parse_quarter_from_content(header_text)
    if quarter is None:
        quarter = _quarter_from_filename(filename)

    # Reporting year — content first
    year = _parse_reporting_year_from_content(header_text, transcript_date)
    if year == transcript_date.year and quarter:
        # Double-check via filename — Q4 calls held in Jan/Feb of next year
        year_from_file = _reporting_year_from_filename(filename, transcript_date)
        if year_from_file != transcript_date.year:
            year = year_from_file

    return TranscriptMetadata(
        filename=filename,
        transcript_date=transcript_date,
        event_type=event_type,
        quarter=quarter,
        year=year,
    )
