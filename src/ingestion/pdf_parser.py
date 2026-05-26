"""
Parse Rockwool FactSet CallStreet transcript PDFs into structured speaker turns.

FactSet transcript structure:
  - Header/cover pages (skip)
  - CORPORATE PARTICIPANTS block
  - OTHER PARTICIPANTS block
  - MANAGEMENT DISCUSSION SECTION
      SpeakerName<role line>
      ... text ...
      dotted separator line
  - QUESTION AND ANSWER SECTION
      SpeakerName<role line> Q / A
      ... text ...
      dotted separator line
  - Disclaimer (skip)
"""

import re
from pathlib import Path

import pdfplumber

from src.ingestion.transcript_metadata import extract_metadata
from src.models.schema import ParsedTranscript, Section, SpeakerTurn, TranscriptMetadata

# FactSet separator line between speaker turns
_SEPARATOR_RE = re.compile(r"\.{20,}")

# Section header markers
_MGMT_HEADER_RE = re.compile(r"MANAGEMENT\s+DISCUSSION\s+SECTION", re.IGNORECASE)
_QA_HEADER_RE = re.compile(r"QUESTION\s+AND\s+ANSWER\s+SECTION", re.IGNORECASE)

# FactSet page header block — appears at top of every page after page 1.
# Pattern: standalone page number line, then company header lines, then copyright.
# We strip the entire block per-page before joining pages.
_FACTSET_HEADER_BLOCK_RE = re.compile(
    r"(?:--\s*\d+\s+of\s+\d+\s*--\s*)?"           # optional "-- N of M --"
    r"(?:Rockwool International|ROCKWOOL)[^\n]*\n"  # company name line
    r"[^\n]*(?:Earnings Call|General Meeting|Analyst Meeting|Extraordinary)[^\n]*\n"
    r"(?:Corrected Transcript|RAW TRANSCRIPT)?\s*"
    r"\d{2}-\w+-\d{4}\s*\n"                        # date line
    r"1-877-FACTSET[^\n]*\n"                        # factset line
    r"\d+\s*\n"                                     # page number
    r"Copyright[^\n]*\n",
    re.IGNORECASE,
)

# Simpler line-level boilerplate patterns
_BOILERPLATE_LINE_RE = re.compile(
    r"^("
    r"1-877-FACTSET.*|"
    r"www\.callstreet\.com.*|"
    r"Copyright\s*©.*|"
    r"--\s*\d+\s+of\s+\d+\s*--|"
    r"Total Pages:.*|"
    r"Corrected Transcript|"
    r"RAW TRANSCRIPT|"
    r"(Rockwool International|ROCKWOOL)\s+(A/S\s*)?\(?ROCK.*|"  # "ROCKWOOL A/S (ROCK..." or "ROCKWOOL A/S"
    r"ROCKWOOL\s+A/S\s*$|"                                      # standalone "ROCKWOOL A/S"
    r"\(?ROCK\.[A-Z]+\.DK\).*|"                                 # "(ROCK.B.DK)..." ticker lines
    r"(Q[1-4]\s+20\d{2}|Full Year 20\d{2})\s+(Earnings Call|Results).*|"
    r"\d{2}-[A-Za-z]+-\d{4}"                                    # date lines like 07-Feb-2025
    r")$",
    re.IGNORECASE,
)

# A standalone page number line (digits only, 1-3 chars)
_PAGE_NUMBER_RE = re.compile(r"^\d{1,3}$")

# Known management roles — used to set is_management flag
_MANAGEMENT_ROLE_KEYWORDS = {
    "chief executive", "ceo", "president", "chief financial", "cfo",
    "chief operating", "coo", "senior vice president", "svp", "executive vice",
    "managing director", "chairman", "director",
}

# Disclaimer start — everything after this is boilerplate
_DISCLAIMER_RE = re.compile(r"^The information herein is based on", re.IGNORECASE)

# Q/A marker that FactSet puts at start of a line or before the speaker name
_QA_MARKER_RE = re.compile(r"^[QA]\s*$")

# FactSet headers are always name + role (max 2 lines). A 3rd line is almost always speech.
_MAX_HEADER_LINES = 2

# Lines that look like spoken content, not speaker metadata
_SPEECH_START_RE = re.compile(
    r"^(ladies and gentlemen|thank you|thanks[,.]?|good (morning|afternoon|day|evening)|"
    r"hi[,.]?|hello|yes[,.]?|no[,.]?|so[,.]?|well[,.]?|okay[,.]?|"
    r"i (think|will|would|can|want|mean|have|am)|we (have|will|are|expect|see)|"
    r"our |the |in |for |first of all|before i|after i|let me|my name is)",
    re.IGNORECASE,
)


def _is_likely_speech_line(line: str) -> bool:
    """True if this line is transcript speech, not a speaker name or role."""
    stripped = line.strip()
    if len(stripped) > 100:
        return True
    if _SPEECH_START_RE.match(stripped):
        return True
    # Role lines are short and contain job-title keywords
    role_keywords = (
        "analyst", "officer", "president", "chief", "director", "senior vice",
        "managing director", "operator", "head of", "vice president",
    )
    lower = stripped.lower()
    if any(kw in lower for kw in role_keywords) and len(stripped) < 120:
        return False
    # A bare person name: 2-4 title-case words, no sentence punctuation mid-line
    if re.match(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}$", stripped):
        return False
    return len(stripped.split()) > 12


# FactSet page-top header lines — these appear at the top of every page > 1
_FACTSET_PAGE_TOP_PATTERNS = [
    re.compile(r"^(Rockwool International|ROCKWOOL)\s+(A/S\s*)?\(?ROCK", re.IGNORECASE),
    re.compile(r"^(Q[1-4]\s+20\d{2}|Full Year 20\d{2})\s+(Earnings Call|Results)", re.IGNORECASE),
    re.compile(r"^(Annual General Meeting|Analyst Meeting|ESG|Extraordinary)", re.IGNORECASE),
    re.compile(r"^(Corrected Transcript|RAW TRANSCRIPT)$", re.IGNORECASE),
    re.compile(r"^\d{2}-\w{3,9}-\d{4}$"),    # date like "07-Feb-2025"
    re.compile(r"^1-877-FACTSET"),
    re.compile(r"^www\.callstreet\.com"),
    re.compile(r"^Copyright\s*©"),
    re.compile(r"^--\s*\d+\s+of\s+\d+\s*--"),
    re.compile(r"^Total Pages:"),
]


def _strip_page_header(page_text: str) -> str:
    """Strip FactSet boilerplate header from the top of a page's text."""
    lines = page_text.splitlines()
    # Strip leading boilerplate lines + one standalone page number
    i = 0
    while i < min(len(lines), 12):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if _PAGE_NUMBER_RE.match(stripped):
            i += 1
            continue
        if any(p.match(stripped) for p in _FACTSET_PAGE_TOP_PATTERNS):
            i += 1
            continue
        break
    return "\n".join(lines[i:])


def _extract_raw_text(pdf_path: Path) -> str:
    """Extract all text from PDF, stripping page headers per page then joining."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
            # Skip header stripping on cover pages (first 2)
            if page_num >= 2:
                text = _strip_page_header(text)
            pages.append(text)
    return "\n".join(pages)


def _split_sections(text: str) -> tuple[str, str]:
    """Split raw text into (mgmt_discussion, qa) sections."""
    mgmt_match = _MGMT_HEADER_RE.search(text)
    qa_match = _QA_HEADER_RE.search(text)

    if mgmt_match and qa_match:
        mgmt_text = text[mgmt_match.end(): qa_match.start()].strip()
        qa_text = text[qa_match.end():].strip()
    elif mgmt_match:
        mgmt_text = text[mgmt_match.end():].strip()
        qa_text = ""
    else:
        # Fallback: treat everything as mgmt discussion
        mgmt_text = text.strip()
        qa_text = ""

    # Strip disclaimer from qa_text
    for i, line in enumerate(qa_text.splitlines()):
        if _DISCLAIMER_RE.match(line.strip()):
            qa_text = "\n".join(qa_text.splitlines()[:i])
            break

    return mgmt_text, qa_text


def _is_boilerplate_block(lines: list[str]) -> bool:
    """Return True if this block is entirely FactSet boilerplate (no real speaker content)."""
    non_empty = [l.strip() for l in lines if l.strip()]
    if not non_empty:
        return True
    boilerplate_count = sum(
        1 for l in non_empty
        if _BOILERPLATE_LINE_RE.match(l) or _PAGE_NUMBER_RE.match(l)
        or re.search(r"1-877-FACTSET|callstreet\.com|Copyright\s*©", l, re.IGNORECASE)
    )
    return boilerplate_count >= len(non_empty) * 0.6


def _parse_speaker_blocks(section_text: str, section: Section) -> list[dict]:
    """
    Split a section into speaker blocks using the dotted separator.
    Returns list of raw dicts with keys: raw_header, text.
    """
    blocks = []
    chunks = _SEPARATOR_RE.split(section_text)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = chunk.splitlines()
        if _is_boilerplate_block(lines):
            continue
        # First non-empty lines (up to 3) form the speaker header
        header_lines = []
        body_lines = []
        in_body = False
        for line in lines:
            stripped = line.strip()
            # Skip boilerplate lines even inside a real block
            if _BOILERPLATE_LINE_RE.match(stripped) or _PAGE_NUMBER_RE.match(stripped):
                continue
            if not stripped:
                if header_lines:
                    in_body = True
                continue
            # Skip standalone Q/A markers as header lines
            if _QA_MARKER_RE.match(stripped) and not header_lines:
                continue
            if not in_body and len(header_lines) < _MAX_HEADER_LINES and not _is_likely_speech_line(stripped):
                header_lines.append(stripped)
            else:
                in_body = True
                body_lines.append(line)
        if header_lines and body_lines:
            blocks.append({
                "raw_header": " | ".join(header_lines),
                "text": "\n".join(body_lines).strip(),
            })
        elif not header_lines and body_lines:
            # All "header" lines were boilerplate — actual speaker is at top of body.
            # Promote first 1-2 body lines to header.
            body_text = "\n".join(body_lines).strip()
            rescued_lines = []
            remaining_lines = []
            in_rescue = True
            for bline in body_text.splitlines():
                bstripped = bline.strip()
                if not bstripped:
                    in_rescue = False
                    remaining_lines.append(bline)
                    continue
                if in_rescue and len(rescued_lines) < _MAX_HEADER_LINES and not _is_likely_speech_line(bstripped):
                    rescued_lines.append(bstripped)
                else:
                    in_rescue = False
                    remaining_lines.append(bline)
            if rescued_lines and remaining_lines:
                blocks.append({
                    "raw_header": " | ".join(rescued_lines),
                    "text": "\n".join(remaining_lines).strip(),
                })
    return blocks


def _parse_speaker_header(raw_header: str) -> tuple[str, str, bool]:
    """
    Extract (speaker_name, speaker_role, is_management) from a raw header string.

    FactSet format examples:
      "Kim Junge Andersen | Chief Financial Officer & Senior Vice President, ROCKWOOL A/S"
      "Jens Birgersson | President & Chief Executive Officer, ROCKWOOL A/S"
      "Ephrem Ravi | Analyst, Citigroup Global Markets Ltd. Q"
      "Operator"
    """
    parts = [p.strip() for p in raw_header.split("|")]
    name = parts[0] if parts else "Unknown"

    # Strip trailing Q/A markers from name or role
    name = re.sub(r"\s+[QA]$", "", name).strip()

    role = parts[1] if len(parts) > 1 else ""
    role = re.sub(r"\s+[QA]$", "", role).strip()

    # If name looks like a role (contains comma + company) and role is empty → swap
    if "," in name and not role:
        role = name
        name = "Unknown"

    role_lower = role.lower()
    is_management = any(kw in role_lower for kw in _MANAGEMENT_ROLE_KEYWORDS)

    if name.strip().lower() == "operator":
        is_management = False

    return name, role, is_management


def _clean_text(text: str) -> str:
    """Normalize whitespace in extracted text."""
    lines = [line.strip() for line in text.splitlines()]
    # Collapse multiple blank lines to one
    cleaned = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                cleaned.append("")
            prev_blank = True
        else:
            cleaned.append(line)
            prev_blank = False
    return "\n".join(cleaned).strip()


def parse_transcript(pdf_path: str | Path) -> ParsedTranscript:
    """
    Full parse of a single transcript PDF.
    Returns a ParsedTranscript with metadata + speaker turns.
    """
    path = Path(pdf_path)
    metadata: TranscriptMetadata = extract_metadata(path)

    raw_text = _extract_raw_text(path)
    mgmt_text, qa_text = _split_sections(raw_text)

    speaker_turns: list[SpeakerTurn] = []
    chunk_index = 0

    for section, section_text in (
        (Section.MGMT_DISCUSSION, mgmt_text),
        (Section.QA, qa_text),
    ):
        if not section_text:
            continue
        blocks = _parse_speaker_blocks(section_text, section)
        for block in blocks:
            name, role, is_mgmt = _parse_speaker_header(block["raw_header"])
            text = _clean_text(block["text"])
            if not text:
                continue
            speaker_turns.append(
                SpeakerTurn(
                    speaker_name=name,
                    speaker_role=role,
                    is_management=is_mgmt,
                    text=text,
                    section=section,
                    chunk_index=chunk_index,
                )
            )
            chunk_index += 1

    total_pages = None
    with pdfplumber.open(path) as pdf:
        total_pages = len(pdf.pages)

    return ParsedTranscript(
        metadata=metadata,
        speaker_turns=speaker_turns,
        mgmt_discussion_text=_clean_text(mgmt_text),
        qa_text=_clean_text(qa_text),
        total_pages=total_pages,
    )
