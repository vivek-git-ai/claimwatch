"""In-memory claim corpus + export to three JSON files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src.models.corpus import (
    ClaimMade,
    ClaimResolution,
    ClaimsMadeFile,
    ClaimsThreadsFile,
    ClaimsWithResolutionsFile,
    ClaimThread,
    ClaimWithResolution,
    CorpusHedgeLevel,
    CorpusResolutionStatus,
)
from src.pipeline.claim_trace import rebuild_threads_with_traces
from src.models.schema import HedgingLevel, Section

CORPUS_DIR = Path("data/claims")
STEPS_DIR = CORPUS_DIR / "steps"


def _hedge_to_corpus(level: HedgingLevel) -> CorpusHedgeLevel:
    mapping = {
        HedgingLevel.HARD: "firm",
        HedgingLevel.SOFT: "soft",
        HedgingLevel.CONDITIONAL: "moderate",
        HedgingLevel.ASPIRATIONAL: "soft",
        HedgingLevel.UNFALSIFIABLE: "soft",
    }
    return mapping.get(level, "soft")


def _slug_thread_id(subject: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:48] or "topic"
    tid = f"T-{base}"
    n = 2
    while tid in existing:
        tid = f"T-{base}-{n}"
        n += 1
    return tid


@dataclass
class PipelineState:
    claims: list[ClaimMade] = field(default_factory=list)
    resolutions: dict[str, ClaimResolution] = field(default_factory=dict)
    threads: dict[str, ClaimThread] = field(default_factory=dict)
    _counter: int = 0

    def next_claim_id(self) -> str:
        self._counter += 1
        return f"RKW-{self._counter:03d}"

    def open_claims(self) -> list[ClaimMade]:
        out = []
        for c in self.claims:
            res = self.resolutions.get(c.claim_id)
            if res is None or res.status == "open":
                out.append(c)
        return out

    def open_threads_summary(self) -> list[dict]:
        summaries = []
        for tid, thread in self.threads.items():
            last_val = ""
            if thread.evolution:
                last_val = thread.evolution[-1].target_value
            summaries.append(
                {
                    "thread_id": tid,
                    "subject": thread.subject,
                    "last_target_value": last_val,
                }
            )
        return summaries

    def assign_thread(self, subject: str, hint: str | None = None) -> str:
        key = (hint or subject).strip().lower()
        for tid, thread in self.threads.items():
            if thread.subject.strip().lower() == key:
                return tid
        existing_ids = set(self.threads.keys())
        tid = _slug_thread_id(subject, existing_ids)
        self.threads[tid] = ClaimThread(
            thread_id=tid,
            subject=subject,
            category="",
        )
        return tid

    def add_claim(
        self,
        *,
        source_doc: str,
        source_section: Section,
        date_made: date,
        speaker: str,
        quote: str,
        paraphrase: str,
        category: str,
        subject: str,
        timeframe: str,
        target_value: str,
        hedge_level: CorpusHedgeLevel,
        falsifiable: str,
        thread_id: str | None = None,
        notes: str | None = None,
    ) -> ClaimMade:
        cid = self.next_claim_id()
        if not thread_id:
            thread_id = self.assign_thread(subject)
        claim = ClaimMade(
            claim_id=cid,
            thread_id=thread_id,
            source_doc=source_doc,
            source_section=source_section,
            date_made=date_made,
            speaker=speaker,
            quote=quote,
            paraphrase=paraphrase,
            category=category,
            subject=subject,
            timeframe=timeframe,
            target_value=target_value,
            hedge_level=hedge_level,
            falsifiable=falsifiable,  # type: ignore[arg-type]
            notes=notes,
        )
        self.claims.append(claim)
        self.resolutions[cid] = ClaimResolution(status="open")
        thread = self.threads[thread_id]
        thread.category = thread.category or category
        thread.claim_ids.append(cid)
        if thread.first_date is None or date_made < thread.first_date:
            thread.first_date = date_made
        if thread.last_date is None or date_made > thread.last_date:
            thread.last_date = date_made
        thread.n_claims = len(thread.claim_ids)
        return claim

    def apply_resolution_update(
        self,
        claim_id: str,
        status: CorpusResolutionStatus,
        *,
        resolved_by: str | None = None,
        evidence_quote: str | None = None,
        notes: str | None = None,
        resolved_at_transcript: str | None = None,
        resolved_at_date: date | None = None,
    ) -> None:
        if claim_id not in self.resolutions:
            return
        res = self.resolutions[claim_id]
        was_open = res.status == "open"
        res.status = status
        if was_open and status != "open":
            if resolved_at_transcript and not res.resolved_at_transcript:
                res.resolved_at_transcript = resolved_at_transcript
            if resolved_at_date and not res.resolved_at_date:
                res.resolved_at_date = resolved_at_date
        if resolved_by and resolved_by not in res.resolved_by:
            res.resolved_by.append(resolved_by)
        if evidence_quote:
            res.evidence_quote = evidence_quote
        if notes:
            res.resolution_notes = notes

    def rebuild_threads(self) -> None:
        if STEPS_DIR.is_dir():
            rebuild_threads_with_traces(
                self.threads, self.claims, self.resolutions, STEPS_DIR
            )
            for thread in self.threads.values():
                thread.first_said_date = thread.first_date
                res_events = [
                    e for e in thread.trace
                    if e.event == "status_changed" and e.status != "open"
                ]
                if res_events:
                    last = res_events[-1]
                    thread.resolved_at_date = last.date
                    thread.resolved_at_transcript = last.transcript_id
            return

        # Fallback without step snapshots
        for tid in list(self.threads.keys()):
            thread = self.threads[tid]
            thread.claim_ids = []
            thread.evolution = []
            thread.trace = []
            thread.n_claims = 0
            thread.first_date = None
            thread.last_date = None
            thread.first_said_date = None
            thread.resolved_at_date = None
            thread.resolved_at_transcript = None

        from src.models.corpus import ThreadEvolutionStep

        for c in sorted(self.claims, key=lambda x: (x.date_made, x.claim_id)):
            tid = c.thread_id or self.assign_thread(c.subject)
            c.thread_id = tid
            thread = self.threads[tid]
            thread.subject = thread.subject or c.subject
            thread.category = thread.category or c.category
            thread.claim_ids.append(c.claim_id)
            res = self.resolutions.get(c.claim_id)
            status: CorpusResolutionStatus = res.status if res else "open"
            thread.evolution.append(
                ThreadEvolutionStep(
                    claim_id=c.claim_id,
                    date=c.date_made,
                    target_value=c.target_value,
                    hedge_level=c.hedge_level,
                    status_after_this_utterance=status,
                )
            )
            if thread.first_date is None or c.date_made < thread.first_date:
                thread.first_date = c.date_made
            if thread.last_date is None or c.date_made > thread.last_date:
                thread.last_date = c.date_made

        for thread in self.threads.values():
            thread.n_claims = len(thread.claim_ids)
            thread.first_said_date = thread.first_date
            if thread.evolution:
                thread.final_status = thread.evolution[-1].status_after_this_utterance
            else:
                thread.final_status = "open"

    def export_step_snapshot(self, transcript_id: str, corpus_label: str) -> Path:
        """Incremental snapshot after each transcript (re-derivable thread state)."""
        STEPS_DIR.mkdir(parents=True, exist_ok=True)
        step_dir = STEPS_DIR / transcript_id
        step_dir.mkdir(parents=True, exist_ok=True)
        self.rebuild_threads()

        made = ClaimsMadeFile(corpus=corpus_label, total_claims=len(self.claims), claims=self.claims)
        (step_dir / "claims_made.json").write_text(
            json.dumps(made.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        threads_file = ClaimsThreadsFile(
            corpus=corpus_label,
            total_threads=len(self.threads),
            threads=list(self.threads.values()),
        )
        (step_dir / "claims_threads.json").write_text(
            json.dumps(threads_file.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        with_res = [
            ClaimWithResolution(**c.model_dump(), resolution=self.resolutions.get(c.claim_id, ClaimResolution()))
            for c in self.claims
        ]
        res_file = ClaimsWithResolutionsFile(corpus=corpus_label, total_claims=len(with_res), claims=with_res)
        (step_dir / "claims_with_resolutions.json").write_text(
            json.dumps(res_file.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return step_dir

    def export_three_files(self, corpus_label: str) -> tuple[Path, Path, Path]:
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        self.rebuild_threads()

        made = ClaimsMadeFile(
            corpus=corpus_label,
            total_claims=len(self.claims),
            claims=self.claims,
        )
        p1 = CORPUS_DIR / "claims_made.json"
        p1.write_text(
            json.dumps(made.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        threads_file = ClaimsThreadsFile(
            corpus=corpus_label,
            total_threads=len(self.threads),
            threads=list(self.threads.values()),
        )
        p2 = CORPUS_DIR / "claims_threads.json"
        p2.write_text(
            json.dumps(threads_file.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        with_res = []
        for c in self.claims:
            res = self.resolutions.get(c.claim_id, ClaimResolution())
            with_res.append(
                ClaimWithResolution(
                    **c.model_dump(),
                    resolution=res,
                )
            )
        res_file = ClaimsWithResolutionsFile(
            corpus=corpus_label,
            total_claims=len(with_res),
            claims=with_res,
        )
        p3 = CORPUS_DIR / "claims_with_resolutions.json"
        p3.write_text(
            json.dumps(res_file.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return p1, p2, p3
