"""Walk-forward pipeline: extract new claims, then resolve prior open (mgmt + Q&A)."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from src.agents.claim_extractor import extract_claims_from_transcript
from src.agents.claim_resolver import resolve_open_claims
from src.agents.claim_extractor import _format_transcript_text
from src.ingestion.parsed_loader import load_parsed_transcript
from src.models.corpus import ClaimMade
from src.pipeline.claim_staleness import (
    DEFAULT_GRACE_DAYS,
    expire_stale_open_claims,
    filter_claims_for_resolver,
)
from src.pipeline.corpus_store import PipelineState, _hedge_to_corpus
LogFn = Callable[[str], None]

def _default_log(msg: str) -> None:
    print(msg, flush=True)


def _summarize_pass2(
    open_before_count: int,
    stale_count: int,
    fed_count: int,
    updates: list,
    open_after_count: int,
) -> str:
    changed = [u for u in updates if u.status != "open"]
    by_status = Counter(u.status for u in changed)
    parts = []
    for key in ("revised", "confirmed", "failed", "partial", "unresolvable"):
        if by_status[key]:
            parts.append(f"{by_status[key]} {key}")
    detail = ", ".join(parts) if parts else "0 status changes"
    return (
        f"Pass 2: {open_before_count} open-before → auto-stale {stale_count}, "
        f"resolver fed {fed_count}, changed {len(changed)} ({detail}), "
        f"{open_after_count} still open"
    )


def run_walk_forward(
    parsed_paths: list[Path],
    *,
    corpus_label: str = "",
    extract_only: bool = False,
    grace_days: int = DEFAULT_GRACE_DAYS,
    log: LogFn | None = None,
) -> PipelineState:
    """
    Per transcript T (chronological):
      1. Expire stale open claims (horizon + grace)
      2. Pass 1 — extract new claims from T (mgmt + Q&A)
      3. Pass 2 — resolve open-from-before vs T, with NEW claims from T for resolved_by ids
    """
    state = PipelineState()
    log = log or _default_log

    if not corpus_label and parsed_paths:
        corpus_label = f"Rockwool, {len(parsed_paths)} transcripts"

    n = len(parsed_paths)
    for step, json_path in enumerate(parsed_paths, start=1):
        tid = json_path.stem
        parsed = load_parsed_transcript(json_path)
        meta = parsed.metadata
        t_label = f"T={step} {meta.transcript_date} {tid}"
        log(f"\n[{step}/{n}] {tid}")
        t0 = time.perf_counter()

        open_before_count = len(state.open_claims())

        stale_n = expire_stale_open_claims(
            state.claims, state.resolutions, meta.transcript_date, grace_days=grace_days
        )
        if stale_n:
            log(f"  [{t_label}] auto-stale: {stale_n} claims (horizon+{grace_days}d grace)")

        claim_ids_before_extract = {c.claim_id for c in state.claims}

        log(f"  [{t_label}] Pass 1: extracting (mgmt + Q&A)...")
        t_ext = time.perf_counter()
        items, ext_usage = extract_claims_from_transcript(
            parsed,
            tid,
            include_qa=True,
            open_threads=state.open_threads_summary(),
        )
        new_claims: list[ClaimMade] = []
        for item in items:
            thread_id = None
            if item.thread_subject_hint:
                thread_id = state.assign_thread(item.subject, item.thread_subject_hint)
            fals = item.falsifiable if item.falsifiable in ("Y", "partial", "N") else "partial"
            claim = state.add_claim(
                source_doc=tid,
                source_section=item.source_section,
                date_made=meta.transcript_date,
                speaker=item.speaker_name,
                quote=item.original_text,
                paraphrase=item.normalized_claim,
                category=item.claim_type.value,
                subject=item.subject,
                timeframe=item.time_horizon,
                target_value=item.target_value,
                hedge_level=_hedge_to_corpus(item.hedging_level),
                falsifiable=fals,
                thread_id=thread_id,
                notes=item.notes,
            )
            new_claims.append(claim)

        log(
            f"  [{t_label}] Pass 1: {len(new_claims)} new claims extracted "
            f"({time.perf_counter() - t_ext:.0f}s, tokens {ext_usage.get('tokens_in', 0)}/{ext_usage.get('tokens_out', 0)})"
        )

        if extract_only:
            log(f"  [{t_label}] Pass 2: skipped (extract_only)")
        else:
            open_from_before = [
                c
                for c in state.claims
                if c.claim_id in claim_ids_before_extract
                and state.resolutions.get(c.claim_id)
                and state.resolutions[c.claim_id].status == "open"
            ]

            if not open_from_before:
                log(
                    f"  [{t_label}] Pass 2: skipped "
                    f"(0 open-from-before; had {open_before_count} before stale, {stale_n} auto-stale)"
                )
                snap = state.export_step_snapshot(tid, corpus_label)
                log(
                    f"  [{t_label}] snapshot → {snap} | "
                    f"cumulative {len(state.claims)} claims, {len(state.threads)} threads "
                    f"({time.perf_counter() - t0:.0f}s)"
                )
                continue

            transcript_text = _format_transcript_text(parsed, include_qa=True)
            fed = filter_claims_for_resolver(
                open_from_before, transcript_text, new_claims=new_claims
            )

            log(f"  [{t_label}] Pass 2: resolving {len(fed)} of {len(open_from_before)} open-before claims...")
            t_res = time.perf_counter()
            updates, res_usage = resolve_open_claims(
                fed,
                parsed,
                tid,
                new_claims=new_claims,
            )

            for u in updates:
                if u.claim_id in state.resolutions:
                    state.apply_resolution_update(
                        u.claim_id,
                        u.status,
                        resolved_by=u.resolved_by_claim_id,
                        evidence_quote=u.evidence_quote,
                        notes=u.notes,
                        resolved_at_transcript=tid,
                        resolved_at_date=meta.transcript_date,
                    )

            open_after = len(state.open_claims())
            log(
                f"  [{t_label}] {_summarize_pass2(open_before_count, stale_n, len(fed), updates, open_after)} "
                f"({time.perf_counter() - t_res:.0f}s, tokens {res_usage.get('tokens_in', 0)}/{res_usage.get('tokens_out', 0)})"
            )

        snap = state.export_step_snapshot(tid, corpus_label)
        log(
            f"  [{t_label}] snapshot → {snap} | "
            f"cumulative {len(state.claims)} claims, {len(state.threads)} threads "
            f"({time.perf_counter() - t0:.0f}s)"
        )

    return state
