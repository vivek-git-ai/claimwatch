"""Build per-claim and per-thread lifecycle traces from walk-forward step snapshots."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.analytics.corpus_loader import load_step_snapshots
from src.models.corpus import (
    ClaimMade,
    ClaimResolution,
    ClaimThread,
    ClaimWithResolution,
    CorpusResolutionStatus,
    ThreadEvolutionStep,
    ThreadTraceEvent,
)


def _claim_in_snap(
    snapshots: list[tuple[str, date, object]],
    claim_id: str,
    transcript_id: str,
) -> ClaimWithResolution | None:
    for tid, _, snap in snapshots:
        if tid != transcript_id:
            continue
        for c in snap.claims:
            if c.claim_id == claim_id:
                return c
    return None


def build_claim_trace_events(
    claim: ClaimMade,
    snapshots: list,
) -> list[ThreadTraceEvent]:
    """Uttered + each status change from step diffs."""
    events: list[ThreadTraceEvent] = []
    prev_status: str | None = None
    seen = False

    for transcript_id, transcript_date, snap in snapshots:
        row = next((c for c in snap.claims if c.claim_id == claim.claim_id), None)
        if row is None:
            continue

        status = row.resolution.status
        if not seen:
            events.append(
                ThreadTraceEvent(
                    event="uttered",
                    claim_id=claim.claim_id,
                    date=claim.date_made,
                    transcript_id=claim.source_doc,
                    speaker=claim.speaker,
                    status="open",
                    target_value=claim.target_value,
                    hedge_level=claim.hedge_level,
                    evidence_quote=None,
                    resolution_notes=None,
                    resolved_by_claim_ids=[],
                )
            )
            seen = True
            if status != "open":
                events.append(
                    ThreadTraceEvent(
                        event="status_changed",
                        claim_id=claim.claim_id,
                        date=transcript_date,
                        transcript_id=transcript_id,
                        speaker=None,
                        status=status,
                        target_value=claim.target_value,
                        hedge_level=claim.hedge_level,
                        evidence_quote=row.resolution.evidence_quote,
                        resolution_notes=row.resolution.resolution_notes,
                        resolved_by_claim_ids=list(row.resolution.resolved_by),
                    )
                )
            prev_status = status
            continue

        if status != prev_status:
            events.append(
                ThreadTraceEvent(
                    event="status_changed",
                    claim_id=claim.claim_id,
                    date=transcript_date,
                    transcript_id=transcript_id,
                    speaker=None,
                    status=status,
                    target_value=claim.target_value,
                    hedge_level=claim.hedge_level,
                    evidence_quote=row.resolution.evidence_quote,
                    resolution_notes=row.resolution.resolution_notes,
                    resolved_by_claim_ids=list(row.resolution.resolved_by),
                )
            )
            prev_status = status

    return events


def first_resolution_from_trace(events: list[ThreadTraceEvent]) -> ThreadTraceEvent | None:
    for ev in events:
        if ev.event == "status_changed" and ev.status != "open":
            return ev
    return None


def enrich_resolution_from_trace(
    claim: ClaimMade,
    resolution: ClaimResolution,
    events: list[ThreadTraceEvent],
) -> ClaimResolution:
    """Fill resolved_at_* from trace if missing."""
    first = first_resolution_from_trace(events)
    if first:
        if resolution.resolved_at_date is None:
            resolution.resolved_at_date = first.date
        if resolution.resolved_at_transcript is None:
            resolution.resolved_at_transcript = first.transcript_id
    resolution.resolved_at_transcript = resolution.resolved_at_transcript or None
    return resolution


def status_after_utterance(
    claim: ClaimMade,
    snapshots: list,
) -> CorpusResolutionStatus:
    """Status at end of the transcript where the claim was first said."""
    row = _claim_in_snap(snapshots, claim.claim_id, claim.source_doc)
    if row:
        return row.resolution.status
    return "open"


def build_thread_trace(
    thread: ClaimThread,
    claims_by_id: dict[str, ClaimMade],
    snapshots: list,
) -> list[ThreadTraceEvent]:
    """All events in thread, chronological."""
    all_events: list[ThreadTraceEvent] = []
    for cid in thread.claim_ids:
        c = claims_by_id.get(cid)
        if not c:
            continue
        all_events.extend(build_claim_trace_events(c, snapshots))
    all_events.sort(key=lambda e: (e.date, e.claim_id, 0 if e.event == "uttered" else 1))
    return all_events


def rebuild_threads_with_traces(
    threads: dict[str, ClaimThread],
    claims: list[ClaimMade],
    resolutions: dict[str, ClaimResolution],
    steps_dir: Path,
) -> None:
    """Rebuild evolution + trace from step snapshots."""
    snapshots = load_step_snapshots(steps_dir)
    if not snapshots:
        return

    claims_by_id = {c.claim_id: c for c in claims}

    for tid in list(threads.keys()):
        threads[tid].claim_ids = []
        threads[tid].evolution = []
        threads[tid].trace = []
        threads[tid].n_claims = 0
        threads[tid].first_date = None
        threads[tid].last_date = None

    for c in sorted(claims, key=lambda x: (x.date_made, x.claim_id)):
        tid = c.thread_id
        if not tid or tid not in threads:
            continue
        thread = threads[tid]
        thread.claim_ids.append(c.claim_id)
        thread.subject = thread.subject or c.subject
        thread.category = thread.category or c.category

        status_at_utterance = status_after_utterance(c, snapshots)
        thread.evolution.append(
            ThreadEvolutionStep(
                claim_id=c.claim_id,
                date=c.date_made,
                target_value=c.target_value,
                hedge_level=c.hedge_level,
                status_after_this_utterance=status_at_utterance,
            )
        )
        if thread.first_date is None or c.date_made < thread.first_date:
            thread.first_date = c.date_made
        if thread.last_date is None or c.date_made > thread.last_date:
            thread.last_date = c.date_made

    for thread in threads.values():
        thread.n_claims = len(thread.claim_ids)
        thread.trace = build_thread_trace(thread, claims_by_id, snapshots)
        if thread.trace:
            last = thread.trace[-1]
            thread.final_status = last.status
        elif thread.evolution:
            thread.final_status = thread.evolution[-1].status_after_this_utterance
        else:
            thread.final_status = "open"

    for c in claims:
        events = build_claim_trace_events(c, snapshots)
        res = resolutions.get(c.claim_id)
        if res:
            enrich_resolution_from_trace(c, res, events)
