"""Locate source PDFs and page numbers for claim quotes."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pdfplumber

PARSED_DIR = Path(os.getenv("PARSED_DIR", "data/parsed"))
TRANSCRIPTS_DIR = Path(os.getenv("TRANSCRIPTS_DIR", "docs/transcripts"))

_DATE_IN_DOC = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _normalize(text: str) -> str:
    t = text.lower().replace("\u2013", "-").replace("\u2014", "-")
    t = re.sub(r"[^\w\s%€$.,\-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class PdfCitation:
    source_doc: str
    pdf_path: Path | None
    pdf_filename: str | None
    page: int | None
    total_pages: int | None
    match_method: str
    speaker_turn_index: int | None = None

    @property
    def found(self) -> bool:
        return self.pdf_path is not None and self.pdf_path.exists()


def _parsed_path(source_doc: str) -> Path:
    return PARSED_DIR / f"{source_doc}.json"


@lru_cache(maxsize=64)
def _load_parsed_meta(source_doc: str) -> tuple[str | None, int | None]:
    path = _parsed_path(source_doc)
    if not path.exists():
        return None, None
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data.get("metadata", {})
    return meta.get("filename"), data.get("total_pages")


def resolve_pdf_path(source_doc: str) -> Path | None:
    """Map transcript stem to PDF under TRANSCRIPTS_DIR."""
    filename, _ = _load_parsed_meta(source_doc)
    if filename:
        direct = TRANSCRIPTS_DIR / filename
        if direct.exists():
            return direct

    if not TRANSCRIPTS_DIR.is_dir():
        return None

    dm = _DATE_IN_DOC.search(source_doc)
    if dm:
        date_str = dm.group(1)
        candidates = list(TRANSCRIPTS_DIR.glob(f"*{date_str}*.pdf"))
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            candidates.sort(key=lambda p: len(p.name))
            return candidates[0]

    return None


def _find_in_parsed_turns(source_doc: str, quote: str) -> int | None:
    path = _parsed_path(source_doc)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    qn = _normalize(quote)
    if len(qn) < 20:
        needle = qn
    else:
        needle = qn[: min(120, len(qn))]

    for turn in data.get("speaker_turns", []):
        text = turn.get("text", "")
        if needle in _normalize(text):
            return int(turn.get("chunk_index", 0))
    return None


@lru_cache(maxsize=512)
def find_quote_page(pdf_path_str: str, quote: str) -> tuple[int | None, str]:
    """Return 1-based page number where quote appears, and match method."""
    path = Path(pdf_path_str)
    if not path.exists():
        return None, "missing_pdf"

    qn = _normalize(quote)
    if not qn:
        return None, "empty_quote"

    needles = [qn]
    if len(qn) > 80:
        needles.append(qn[:80])
    if len(qn) > 40:
        needles.append(qn[:40])

    try:
        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = _normalize(page.extract_text(x_tolerance=2, y_tolerance=2) or "")
                if not text:
                    continue
                for needle in needles:
                    if needle in text:
                        return page_num, "pdf_text"
    except Exception:
        return None, "pdf_error"

    return None, "not_found"


def get_citation(source_doc: str, quote: str) -> PdfCitation:
    pdf_path = resolve_pdf_path(source_doc)
    filename, total_pages = _load_parsed_meta(source_doc)
    turn_idx = _find_in_parsed_turns(source_doc, quote)

    page: int | None = None
    method = "none"
    if pdf_path:
        page, method = find_quote_page(str(pdf_path.resolve()), quote)
        if page is None and turn_idx is not None:
            method = "turn_only"

    return PdfCitation(
        source_doc=source_doc,
        pdf_path=pdf_path,
        pdf_filename=filename or (pdf_path.name if pdf_path else None),
        page=page,
        total_pages=total_pages,
        match_method=method,
        speaker_turn_index=turn_idx,
    )


def render_page_image(pdf_path: Path, page: int, resolution: int = 120):
    """PIL Image for a single PDF page (1-based)."""
    with pdfplumber.open(pdf_path) as pdf:
        if page < 1 or page > len(pdf.pages):
            return None
        return pdf.pages[page - 1].to_image(resolution=resolution).original
