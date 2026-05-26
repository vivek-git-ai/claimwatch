"""Speaker-turn excerpts with quote highlighting from parsed JSON."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

from src.analytics.pdf_citation import PARSED_DIR, _normalize


@dataclass
class TranscriptExcerpt:
    source_doc: str
    speaker_name: str
    speaker_role: str
    section: str
    chunk_index: int | None
    turn_text: str
    highlighted_html: str
    match_quality: str  # exact | partial | none


def _needle_variants(quote: str) -> list[str]:
    qn = _normalize(quote)
    if not qn:
        return []
    variants = [quote.strip(), qn]
    if len(qn) > 120:
        variants.append(quote.strip()[:120])
    if len(qn) > 60:
        variants.append(qn[:60])
    seen: set[str] = set()
    out = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _find_turn_for_quote(source_doc: str, quote: str) -> dict | None:
    path = PARSED_DIR / f"{source_doc}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    turns = data.get("speaker_turns", [])
    if not turns:
        return None

    best: dict | None = None
    best_len = 0
    for turn in turns:
        text = turn.get("text", "")
        nt = _normalize(text)
        for needle in _needle_variants(quote):
            nn = _normalize(needle)
            if nn in nt and len(nn) > best_len:
                best = turn
                best_len = len(nn)
    return best


def _highlight_in_text(text: str, quote: str) -> tuple[str, str]:
    """Return (html, match_quality)."""
    if not text or not quote:
        return html.escape(text), "none"

    for quality, needle in [("exact", quote.strip()), ("partial", "")]:
        if quality == "partial":
            variants = _needle_variants(quote)
            needle = variants[-1] if variants else ""
            if len(needle) < 20 and variants:
                needle = variants[0]
        if not needle:
            continue

        # Case-insensitive search on original text
        pattern = re.escape(needle)
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not m:
            # Try normalized whitespace-tolerant match
            words = needle.split()[:12]
            if len(words) >= 4:
                loose = r"\s+".join(re.escape(w) for w in words)
                m = re.search(loose, text, re.IGNORECASE | re.DOTALL)
        if m:
            before = html.escape(text[: m.start()])
            mid = html.escape(text[m.start() : m.end()])
            after = html.escape(text[m.end() :])
            return (
                f'{before}<mark class="cw-highlight">{mid}</mark>{after}',
                quality if m.group(0).lower() == needle.lower() else "partial",
            )

    return html.escape(text), "none"


def get_transcript_excerpt(source_doc: str, quote: str) -> TranscriptExcerpt | None:
    turn = _find_turn_for_quote(source_doc, quote)
    if not turn:
        return None

    text = turn.get("text", "")
    highlighted, quality = _highlight_in_text(text, quote)
    section = turn.get("section", "unknown")
    if hasattr(section, "value"):
        section = section.value

    return TranscriptExcerpt(
        source_doc=source_doc,
        speaker_name=turn.get("speaker_name", ""),
        speaker_role=turn.get("speaker_role", ""),
        section=str(section),
        chunk_index=turn.get("chunk_index"),
        turn_text=text,
        highlighted_html=highlighted,
        match_quality=quality,
    )
