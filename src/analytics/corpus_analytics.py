"""Aggregations and resolution timelines over the claim corpus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from src.analytics.corpus_loader import CorpusBundle, EnrichedClaim
from src.analytics.status_mapping import pdf_outcome_label

TERMINAL_STATUSES = frozenset(
    {"confirmed", "revised", "failed", "partial", "unresolvable", "stale", "n/a"}
)
RESOLVED_STATUSES = TERMINAL_STATUSES - {"open"}


@dataclass
class TimelineEvent:
    transcript_id: str
    transcript_date: date
    status: str
    evidence_quote: str | None = None


def build_resolution_timelines(bundle: CorpusBundle) -> dict[str, list[TimelineEvent]]:
    """
    Diff step snapshots: record status when it changes per claim_id.
    """
    timelines: dict[str, list[TimelineEvent]] = {}
    prev_status: dict[str, str] = {}

    for transcript_id, transcript_date, snap in bundle.step_snapshots:
        for c in snap.claims:
            cid = c.claim_id
            status = c.resolution.status
            if prev_status.get(cid) != status:
                timelines.setdefault(cid, []).append(
                    TimelineEvent(
                        transcript_id=transcript_id,
                        transcript_date=transcript_date,
                        status=status,
                        evidence_quote=c.resolution.evidence_quote,
                    )
                )
                prev_status[cid] = status

    return timelines


def first_resolution_event(
    timelines: dict[str, list[TimelineEvent]],
    claim_id: str,
) -> TimelineEvent | None:
    """First step where status leaves open."""
    for ev in timelines.get(claim_id, []):
        if ev.status != "open":
            return ev
    return None


def claims_to_dataframe(bundle: CorpusBundle) -> pd.DataFrame:
    rows = []
    for ec in bundle.enriched:
        c = ec.claim
        rows.append(
            {
                "claim_id": c.claim_id,
                "thread_id": c.thread_id,
                "date_made": c.date_made,
                "calendar_year": ec.calendar_year,
                "doc_year": ec.doc_year,
                "quarter": ec.quarter or "",
                "event_type": ec.event_type,
                "source_doc": c.source_doc,
                "source_section": c.source_section,
                "speaker": c.speaker,
                "category": c.category,
                "subject": c.subject,
                "timeframe": c.timeframe,
                "target_value": c.target_value,
                "hedge_level": c.hedge_level,
                "falsifiable": c.falsifiable,
                "status": c.resolution.status,
                "outcome": pdf_outcome_label(c.resolution.status),
                "resolved_by": ",".join(c.resolution.resolved_by),
                "paraphrase": c.paraphrase,
                "quote": c.quote,
            }
        )
    return pd.DataFrame(rows)


def status_counts(bundle: CorpusBundle) -> pd.Series:
    return pd.Series([ec.status for ec in bundle.enriched]).value_counts()


def claims_by_calendar_year(bundle: CorpusBundle) -> pd.DataFrame:
    df = claims_to_dataframe(bundle)
    return (
        df.groupby("calendar_year", as_index=False)
        .size()
        .rename(columns={"size": "claims"})
        .sort_values("calendar_year")
    )


def claims_by_quarter(bundle: CorpusBundle) -> pd.DataFrame:
    df = claims_to_dataframe(bundle)
    df = df[df["quarter"] != ""].copy()
    if df.empty:
        return pd.DataFrame(columns=["doc_year", "quarter", "claims"])
    df["label"] = df["doc_year"].astype(str) + " " + df["quarter"]
    return (
        df.groupby(["doc_year", "quarter", "label"], as_index=False)
        .size()
        .rename(columns={"size": "claims"})
        .sort_values(["doc_year", "quarter"])
    )


def resolution_summary(bundle: CorpusBundle) -> dict:
    statuses = status_counts(bundle)
    total = len(bundle.enriched)
    open_n = int(statuses.get("open", 0))
    terminal = sum(int(statuses.get(s, 0)) for s in TERMINAL_STATUSES)
    return {
        "total": total,
        "open": open_n,
        "terminal": terminal,
        "pct_resolved": round(100 * (total - open_n) / total, 1) if total else 0,
        "by_status": statuses.to_dict(),
    }


def status_by_category(bundle: CorpusBundle) -> pd.DataFrame:
    df = claims_to_dataframe(bundle)
    return pd.crosstab(df["category"], df["status"], margins=False)


def hedge_status_crosstab(bundle: CorpusBundle) -> pd.DataFrame:
    df = claims_to_dataframe(bundle)
    return pd.crosstab(df["hedge_level"], df["status"], margins=False)


def speaker_stats(bundle: CorpusBundle) -> pd.DataFrame:
    df = claims_to_dataframe(bundle)
    rows = []
    for speaker, grp in df.groupby("speaker"):
        total = len(grp)
        confirmed = (grp["status"] == "confirmed").sum()
        failed = (grp["status"] == "failed").sum()
        open_n = (grp["status"] == "open").sum()
        rows.append(
            {
                "speaker": speaker,
                "claims": total,
                "confirmed": int(confirmed),
                "failed": int(failed),
                "open": int(open_n),
                "confirm_rate_pct": round(100 * confirmed / total, 1) if total else 0,
            }
        )
    return pd.DataFrame(rows).sort_values("claims", ascending=False)


def time_to_resolution_df(
    bundle: CorpusBundle,
    timelines: dict[str, list[TimelineEvent]] | None = None,
) -> pd.DataFrame:
    timelines = timelines or build_resolution_timelines(bundle)
    rows = []
    for ec in bundle.enriched:
        if ec.status == "open":
            continue
        first = first_resolution_event(timelines, ec.claim_id)
        if not first:
            continue
        days = (first.transcript_date - ec.date_made).days
        rows.append(
            {
                "claim_id": ec.claim_id,
                "date_made": ec.date_made,
                "resolved_at": first.transcript_date,
                "resolved_in": first.transcript_id,
                "final_status": ec.status,
                "days_to_resolve": max(days, 0),
                "quarters_to_resolve": round(max(days, 0) / 91, 1),
            }
        )
    return pd.DataFrame(rows)


def open_by_category(bundle: CorpusBundle, top_n: int = 15) -> pd.Series:
    df = claims_to_dataframe(bundle)
    open_df = df[df["status"] == "open"]
    return open_df["category"].value_counts().head(top_n)


def successor_claims(
    bundle: CorpusBundle,
    claim: EnrichedClaim,
) -> list[EnrichedClaim]:
    out = []
    for rid in claim.claim.resolution.resolved_by:
        if rid in bundle.claim_by_id:
            out.append(bundle.claim_by_id[rid])
    return out
