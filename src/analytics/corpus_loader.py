"""Load claim corpus JSON and step snapshots for analytics."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src.models.corpus import (
    ClaimMade,
    ClaimThread,
    ClaimWithResolution,
    ClaimsMadeFile,
    ClaimsThreadsFile,
    ClaimsWithResolutionsFile,
)

_DOC_YEAR_RE = re.compile(r"_(\d{4})-\d{2}-\d{2}_")
_QUARTER_RE = re.compile(r"earnings_call_(Q[1-4])", re.IGNORECASE)
_EVENT_PATTERNS: list[tuple[str, str]] = [
    ("earnings_call", "earnings_call"),
    ("esg_meeting", "esg_meeting"),
    ("agm", "agm"),
    ("extraordinary_meeting", "extraordinary_meeting"),
    ("analyst", "analyst_meeting"),
]


def default_corpus_dir() -> Path:
    return Path(os.getenv("CLAIMWATCH_DATA_DIR", "data/claims"))


def parse_source_doc(source_doc: str) -> dict:
    """Derive year, quarter, event_type from transcript filename stem."""
    year: int | None = None
    m = _DOC_YEAR_RE.search(source_doc)
    if m:
        year = int(m.group(1))

    quarter: str | None = None
    qm = _QUARTER_RE.search(source_doc)
    if qm:
        quarter = qm.group(1).upper()

    event_type = "other"
    lower = source_doc.lower()
    for needle, label in _EVENT_PATTERNS:
        if needle in lower:
            event_type = label
            break

    return {"year": year, "quarter": quarter, "event_type": event_type}


@dataclass
class EnrichedClaim:
    """Claim with resolution + parsed doc metadata for analytics."""

    claim: ClaimWithResolution
    calendar_year: int
    doc_year: int | None
    quarter: str | None
    event_type: str

    @property
    def claim_id(self) -> str:
        return self.claim.claim_id

    @property
    def status(self) -> str:
        return self.claim.resolution.status

    @property
    def speaker(self) -> str:
        return self.claim.speaker

    @property
    def date_made(self) -> date:
        return self.claim.date_made


@dataclass
class CorpusBundle:
    """Loaded corpus with indexes."""

    corpus_dir: Path
    corpus_label: str
    claims_made: ClaimsMadeFile
    claims_threads: ClaimsThreadsFile
    claims_with_resolutions: ClaimsWithResolutionsFile
    enriched: list[EnrichedClaim] = field(default_factory=list)
    claim_by_id: dict[str, EnrichedClaim] = field(default_factory=dict)
    thread_by_id: dict[str, ClaimThread] = field(default_factory=dict)
    step_snapshots: list[tuple[str, date, ClaimsWithResolutionsFile]] = field(default_factory=list)

    @property
    def total_claims(self) -> int:
        return len(self.enriched)

    def claims_for_thread(self, thread_id: str) -> list[EnrichedClaim]:
        return [c for c in self.enriched if c.claim.thread_id == thread_id]


def _load_json(path: Path, model_cls):
    if not path.exists():
        raise FileNotFoundError(f"Missing corpus file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return model_cls.model_validate(data)


def _step_transcript_date(stem: str) -> date:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", stem)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return date.min


def load_step_snapshots(steps_dir: Path) -> list[tuple[str, date, ClaimsWithResolutionsFile]]:
    if not steps_dir.is_dir():
        return []
    entries: list[tuple[str, date, ClaimsWithResolutionsFile]] = []
    for step_dir in sorted(steps_dir.iterdir()):
        if not step_dir.is_dir():
            continue
        snap = step_dir / "claims_with_resolutions.json"
        if not snap.exists():
            continue
        tid = step_dir.name
        entries.append(
            (
                tid,
                _step_transcript_date(tid),
                ClaimsWithResolutionsFile.model_validate(
                    json.loads(snap.read_text(encoding="utf-8"))
                ),
            )
        )
    entries.sort(key=lambda x: x[1])
    return entries


def load_corpus(corpus_dir: Path | None = None) -> CorpusBundle:
    root = corpus_dir or default_corpus_dir()
    made = _load_json(root / "claims_made.json", ClaimsMadeFile)
    threads = _load_json(root / "claims_threads.json", ClaimsThreadsFile)
    with_res = _load_json(root / "claims_with_resolutions.json", ClaimsWithResolutionsFile)

    enriched: list[EnrichedClaim] = []
    claim_by_id: dict[str, EnrichedClaim] = {}
    for c in with_res.claims:
        meta = parse_source_doc(c.source_doc)
        ec = EnrichedClaim(
            claim=c,
            calendar_year=c.date_made.year,
            doc_year=meta["year"],
            quarter=meta["quarter"],
            event_type=meta["event_type"],
        )
        enriched.append(ec)
        claim_by_id[c.claim_id] = ec

    thread_by_id = {t.thread_id: t for t in threads.threads}
    steps = load_step_snapshots(root / "steps")

    return CorpusBundle(
        corpus_dir=root,
        corpus_label=with_res.corpus or made.corpus,
        claims_made=made,
        claims_threads=threads,
        claims_with_resolutions=with_res,
        enriched=enriched,
        claim_by_id=claim_by_id,
        thread_by_id=thread_by_id,
        step_snapshots=steps,
    )


def corpus_available(corpus_dir: Path | None = None) -> bool:
    root = corpus_dir or default_corpus_dir()
    return (root / "claims_with_resolutions.json").exists()
