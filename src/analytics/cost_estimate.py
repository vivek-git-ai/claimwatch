"""Estimate Azure GPT-4o-mini pipeline cost from parsed transcripts and step snapshots."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from src.agents.claim_extractor import _format_transcript_text
from src.analytics.corpus_loader import CorpusBundle, load_corpus
from src.ingestion.parsed_loader import load_parsed_transcript

# List pricing (USD per 1M tokens) — Azure OpenAI gpt-4o-mini, global standard tier.
# Override via env if your deployment differs.
DEFAULT_INPUT_USD_PER_1M = 0.15
DEFAULT_OUTPUT_USD_PER_1M = 0.60

MODEL_LABEL = "gpt-4o-mini"

# Fixed overhead from system prompts + user wrapper (tokens).
_EXTRACT_SYSTEM_TOKENS = 520
_EXTRACT_USER_WRAPPER = 80
_RESOLVE_SYSTEM_TOKENS = 380
_RESOLVE_USER_WRAPPER = 200
_CHARS_PER_TOKEN = 4
_EXTRACT_TOKENS_PER_CLAIM_OUT = 85
_RESOLVE_TOKENS_PER_FED_CLAIM_IN = 115
_RESOLVE_TOKENS_PER_FED_CLAIM_OUT = 22
_RESOLVE_OUTPUT_BASE = 35
_MAX_FED_OPEN = 35


def _pricing() -> tuple[float, float]:
    return (
        float(os.getenv("AZURE_MINI_INPUT_USD_PER_1M", str(DEFAULT_INPUT_USD_PER_1M))),
        float(os.getenv("AZURE_MINI_OUTPUT_USD_PER_1M", str(DEFAULT_OUTPUT_USD_PER_1M))),
    )


def tokens_from_chars(n_chars: int) -> int:
    return max(1, n_chars // _CHARS_PER_TOKEN)


def usd_from_tokens(tokens_in: int, tokens_out: int) -> float:
    pin, pout = _pricing()
    return (tokens_in * pin + tokens_out * pout) / 1_000_000


@dataclass
class StepCostEstimate:
    transcript_id: str
    transcript_date: str
    extract_tokens_in: int
    extract_tokens_out: int
    extract_usd: float
    resolve_tokens_in: int
    resolve_tokens_out: int
    resolve_usd: float
    open_before: int
    fed_to_resolver: int
    new_claims: int
    mgmt_chars: int

    @property
    def total_usd(self) -> float:
        return self.extract_usd + self.resolve_usd


@dataclass
class CorpusCostReport:
    model: str
    input_usd_per_1m: float
    output_usd_per_1m: float
    n_transcripts: int
    n_claims: int
    steps: list[StepCostEstimate]
    observed_extract_usd: float | None
    source_note: str

    @property
    def extract_tokens_in(self) -> int:
        return sum(s.extract_tokens_in for s in self.steps)

    @property
    def extract_tokens_out(self) -> int:
        return sum(s.extract_tokens_out for s in self.steps)

    @property
    def resolve_tokens_in(self) -> int:
        return sum(s.resolve_tokens_in for s in self.steps)

    @property
    def resolve_tokens_out(self) -> int:
        return sum(s.resolve_tokens_out for s in self.steps)

    @property
    def extract_usd(self) -> float:
        return sum(s.extract_usd for s in self.steps)

    @property
    def resolve_usd(self) -> float:
        return sum(s.resolve_usd for s in self.steps)

    @property
    def total_usd(self) -> float:
        return self.extract_usd + self.resolve_usd

    @property
    def per_transcript_usd(self) -> float:
        return self.total_usd / self.n_transcripts if self.n_transcripts else 0.0


def _fed_open_count(open_before: int) -> int:
    if open_before <= 0:
        return 0
    # Resolver pre-filter caps relevance-scored open claims.
    return min(open_before, _MAX_FED_OPEN)


def estimate_extract_tokens(mgmt_chars: int, new_claims: int) -> tuple[int, int]:
    tin = _EXTRACT_SYSTEM_TOKENS + _EXTRACT_USER_WRAPPER + tokens_from_chars(mgmt_chars)
    tout = 60 + new_claims * _EXTRACT_TOKENS_PER_CLAIM_OUT
    return tin, tout


def estimate_resolve_tokens(mgmt_chars: int, fed_open: int) -> tuple[int, int]:
    if fed_open <= 0:
        return 0, 0
    tin = (
        _RESOLVE_SYSTEM_TOKENS
        + _RESOLVE_USER_WRAPPER
        + tokens_from_chars(mgmt_chars)
        + fed_open * _RESOLVE_TOKENS_PER_FED_CLAIM_IN
    )
    tout = _RESOLVE_OUTPUT_BASE + fed_open * _RESOLVE_TOKENS_PER_FED_CLAIM_OUT
    return tin, tout


def _load_observed_extract_usd() -> float | None:
    path = Path("data/stats/extraction_summary.json")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("transcripts") or []
        if not rows or "cost_usd" not in rows[0]:
            return None
        return round(sum(float(r.get("cost_usd", 0)) for r in rows), 4)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _count_open(snapshot) -> int:
    return sum(1 for c in snapshot.claims if c.resolution.status == "open")


def _count_new_at_step(snapshot, transcript_id: str) -> int:
    return sum(1 for c in snapshot.claims if c.source_doc == transcript_id)


def build_corpus_cost_report(
    bundle: CorpusBundle | None = None,
    *,
    parsed_dir: Path | None = None,
) -> CorpusCostReport:
    """Walk-forward cost model using step snapshots + parsed transcript sizes."""
    bundle = bundle or load_corpus()
    parsed_dir = parsed_dir or Path(os.getenv("PARSED_DIR", "data/parsed"))
    pin, pout = _pricing()

    steps_out: list[StepCostEstimate] = []
    prev_open = 0

    for tid, tdate, snap in bundle.step_snapshots:
        parsed_path = parsed_dir / f"{tid}.json"
        if parsed_path.is_file():
            parsed = load_parsed_transcript(parsed_path)
            mgmt_text = _format_transcript_text(parsed, include_qa=True)
            mgmt_chars = len(mgmt_text)
        else:
            mgmt_chars = 40_000

        new_claims = _count_new_at_step(snap, tid)
        ext_in, ext_out = estimate_extract_tokens(mgmt_chars, max(new_claims, 1))
        ext_usd = usd_from_tokens(ext_in, ext_out)

        fed = _fed_open_count(prev_open)
        res_in, res_out = estimate_resolve_tokens(mgmt_chars, fed)
        res_usd = usd_from_tokens(res_in, res_out)

        steps_out.append(
            StepCostEstimate(
                transcript_id=tid,
                transcript_date=str(tdate),
                extract_tokens_in=ext_in,
                extract_tokens_out=ext_out,
                extract_usd=round(ext_usd, 4),
                resolve_tokens_in=res_in,
                resolve_tokens_out=res_out,
                resolve_usd=round(res_usd, 4),
                open_before=prev_open,
                fed_to_resolver=fed,
                new_claims=new_claims,
                mgmt_chars=mgmt_chars,
            )
        )
        prev_open = _count_open(snap)

    n = len(steps_out) or 1
    note = (
        f"Estimated from {len(steps_out)} walk-forward steps (extract + resolve per transcript). "
        f"Pricing ${pin}/1M in, ${pout}/1M out. Resolver assumes ≤{_MAX_FED_OPEN} open claims fed per step."
    )
    return CorpusCostReport(
        model=MODEL_LABEL,
        input_usd_per_1m=pin,
        output_usd_per_1m=pout,
        n_transcripts=len(steps_out),
        n_claims=bundle.total_claims,
        steps=steps_out,
        observed_extract_usd=_load_observed_extract_usd(),
        source_note=note,
    )


def scale_projection(report: CorpusCostReport, doc_counts: list[int]) -> list[dict]:
    """Linear scale by document count using average $/transcript from this corpus."""
    per_doc = report.per_transcript_usd
    per_doc_ext = report.extract_usd / report.n_transcripts if report.n_transcripts else 0
    per_doc_res = report.resolve_usd / report.n_transcripts if report.n_transcripts else 0
    rows = []
    for n in doc_counts:
        rows.append(
            {
                "documents": n,
                "extract_usd": round(per_doc_ext * n, 2),
                "resolve_usd": round(per_doc_res * n, 2),
                "total_usd": round(per_doc * n, 2),
            }
        )
    return rows
