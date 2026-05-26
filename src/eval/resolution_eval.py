"""Pass 2: resolution evaluation (single checkpoint).

Compares the predicted post-checkpoint resolutions in
`data/claims/steps/<checkpoint>/claims_with_resolutions.json` against the gold
checkpoint file `data/eval/gold/resolution/checkpoint_after_04.json`.

Matcher (deterministic pairing + status checks on matched pairs).

LLM judge (primary quality signal on matched pairs):
  - reproduces_gold_status, evidence_relevant, resolution_contextually_sound

Outputs:
  - data/stats/resolution_eval_report.txt
  - data/stats/resolution_eval_results.json
  - data/eval/results.json (via `eval`)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.eval.gold_loader import (
    GoldExtractionFile,
    GoldResolutionFile,
    load_extraction_gold_dir,
    load_resolution_checkpoint,
)
from src.eval.matcher import (
    MatchPair,
    match_predictions_to_gold,
    quote_locate_rate,
    text_similarity,
)


# ------------------------- types -------------------------


_TERMINAL_STATUSES = {"confirmed", "revised", "failed", "partial"}


@dataclass
class ResolutionEvalResult:
    checkpoint_transcript_id: str
    reviewed: bool
    labelled_by: str
    n_gold: int
    n_matched: int
    status_correct: int
    status_total: int
    resolved_at_correct: int
    resolved_at_total: int
    evidence_locate_hits: int
    evidence_locate_total: int
    evidence_overlap_hits: int
    evidence_overlap_total: int
    revised_link_correct: int
    revised_link_total: int
    false_open: int
    false_close: int
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    llm_stats: dict[str, int] = field(default_factory=dict)
    pairs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status_accuracy(self) -> float:
        return self.status_correct / self.status_total if self.status_total else 0.0

    @property
    def resolved_at_accuracy(self) -> float:
        return (
            self.resolved_at_correct / self.resolved_at_total
            if self.resolved_at_total
            else 0.0
        )

    @property
    def evidence_locate_pct(self) -> float:
        return (
            self.evidence_locate_hits / self.evidence_locate_total
            if self.evidence_locate_total
            else 0.0
        )

    @property
    def evidence_overlap_pct(self) -> float:
        return (
            self.evidence_overlap_hits / self.evidence_overlap_total
            if self.evidence_overlap_total
            else 0.0
        )

    @property
    def revised_link_pct(self) -> float:
        return (
            self.revised_link_correct / self.revised_link_total
            if self.revised_link_total
            else 0.0
        )


# --------------------- helpers ---------------------


def _load_predictions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("claims", [])


def _load_parsed_text(parsed_dir: Path, transcript_id: str | None) -> str:
    if not transcript_id:
        return ""
    path = parsed_dir / f"{transcript_id}.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    return (data.get("mgmt_discussion_text") or "") + "\n\n" + (data.get("qa_text") or "")


def _build_gold_extraction_index(
    extraction_files: list[GoldExtractionFile],
) -> dict[str, dict[str, Any]]:
    """Map gold_id -> extraction-gold claim dict (used for revised-link lookup)."""
    out: dict[str, dict[str, Any]] = {}
    for gf in extraction_files:
        for c in gf.claims:
            out[c.claim_id] = c.model_dump()
    return out


def _gold_rows_with_full_quote(
    gold_resolution: GoldResolutionFile,
    extraction_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build matching keys for the resolution gold. Each gold row needs `quote` and
    `paraphrase` to drive the matcher (they may already be present, but we backfill
    from extraction gold via claim_id for robustness)."""
    rows: list[dict[str, Any]] = []
    for c in gold_resolution.claims:
        d = c.model_dump()
        backing = extraction_index.get(c.claim_id, {})
        if not d.get("quote"):
            d["quote"] = backing.get("quote", "")
        if not d.get("paraphrase"):
            d["paraphrase"] = backing.get("paraphrase", "")
        if not d.get("source_doc"):
            d["source_doc"] = backing.get("source_doc", "")
        rows.append(d)
    return rows


def _pred_resolution_status(pred: dict[str, Any]) -> str:
    return (pred.get("resolution") or {}).get("status") or "open"


def _pred_resolved_at_transcript(pred: dict[str, Any]) -> str | None:
    return (pred.get("resolution") or {}).get("resolved_at_transcript")


def _pred_resolved_by(pred: dict[str, Any]) -> list[str]:
    return (pred.get("resolution") or {}).get("resolved_by") or []


def _pred_evidence_quote(pred: dict[str, Any]) -> str | None:
    return (pred.get("resolution") or {}).get("evidence_quote")


def _evaluate_pairs(
    matches: list[MatchPair],
    extraction_index: dict[str, dict[str, Any]],
    parsed_dir: Path,
    *,
    similarity: float,
) -> ResolutionEvalResult:
    from src.eval.llm_judge import judge_resolution_pair

    confusion: dict[str, dict[str, int]] = {}
    llm_stats = {
        "reproduces_gold_status": 0,
        "evidence_relevant": 0,
        "resolution_contextually_sound": 0,
        "judged": 0,
    }
    pairs_out: list[dict[str, Any]] = []

    status_total = status_correct = 0
    resolved_at_total = resolved_at_correct = 0
    evidence_locate_hits = evidence_locate_total = 0
    evidence_overlap_hits = evidence_overlap_total = 0
    revised_link_correct = revised_link_total = 0
    false_open = false_close = 0

    for m in matches:
        gold = m.gold
        pred = m.pred
        gold_status = (gold.get("expected_status") or "open").lower()
        pair_record: dict[str, Any] = {
            "gold_id": gold.get("claim_id"),
            "gold_status": gold_status,
            "matched": pred is not None,
            "match_score": round(m.score, 4),
        }
        if pred is None:
            pairs_out.append(pair_record)
            continue

        pred_status = (_pred_resolution_status(pred) or "open").lower()
        pair_record["pred_status"] = pred_status
        pair_record["pred_claim_id"] = pred.get("claim_id")

        confusion.setdefault(gold_status, {}).setdefault(pred_status, 0)
        confusion[gold_status][pred_status] += 1

        status_total += 1
        if pred_status == gold_status:
            status_correct += 1

        gold_resolved_at = gold.get("expected_resolved_at_transcript")
        if gold_status != "open" and gold_resolved_at:
            resolved_at_total += 1
            if _pred_resolved_at_transcript(pred) == gold_resolved_at:
                resolved_at_correct += 1
            pair_record["gold_resolved_at"] = gold_resolved_at
            pair_record["pred_resolved_at"] = _pred_resolved_at_transcript(pred)

        gold_evidence = gold.get("expected_evidence_quote")
        if gold_evidence and gold_resolved_at:
            parsed_text = _load_parsed_text(parsed_dir, gold_resolved_at)
            located, score = quote_locate_rate(
                gold_evidence, parsed_text, similarity=0.80
            )
            evidence_locate_total += 1
            if located:
                evidence_locate_hits += 1
            pair_record["gold_evidence_located"] = located
            pair_record["gold_evidence_locate_score"] = round(score, 4)

        if (
            gold_status in _TERMINAL_STATUSES
            and pred_status in _TERMINAL_STATUSES
            and gold_evidence
        ):
            pred_evidence = _pred_evidence_quote(pred)
            if pred_evidence:
                overlap = text_similarity(gold_evidence, pred_evidence)
                evidence_overlap_total += 1
                if overlap >= 0.85:
                    evidence_overlap_hits += 1
                pair_record["evidence_overlap"] = round(overlap, 4)

        if gold_status == "revised":
            revised_link_total += 1
            gold_successor = gold.get("expected_resolved_by_gold_id")
            pred_resolved_by = _pred_resolved_by(pred)
            successor_quote = (
                extraction_index.get(gold_successor or "", {}).get("paraphrase")
                if gold_successor
                else None
            )
            successor_quote_q = (
                extraction_index.get(gold_successor or "", {}).get("quote")
                if gold_successor
                else None
            )
            ok = False
            if gold_successor and pred_resolved_by:
                ok = any(
                    text_similarity(p, gold_successor) >= 0.95
                    or (
                        successor_quote
                        and text_similarity(p, successor_quote) >= similarity
                    )
                    or (
                        successor_quote_q
                        and text_similarity(p, successor_quote_q) >= similarity
                    )
                    for p in pred_resolved_by
                )
            if ok:
                revised_link_correct += 1
            pair_record["revised_link_ok"] = ok
            pair_record["gold_successor"] = gold_successor
            pair_record["pred_resolved_by"] = pred_resolved_by

        if gold_status in _TERMINAL_STATUSES and pred_status == "open":
            false_open += 1
            pair_record["false_open"] = True
        if gold_status == "open" and pred_status in _TERMINAL_STATUSES:
            false_close += 1
            pair_record["false_close"] = True

        try:
            verdict = judge_resolution_pair(gold, pred)
        except Exception as exc:  # noqa: BLE001
            pair_record["llm_error"] = str(exc)
        else:
            llm_stats["judged"] += 1
            if verdict.reproduces_gold_status:
                llm_stats["reproduces_gold_status"] += 1
            if verdict.evidence_relevant:
                llm_stats["evidence_relevant"] += 1
            if verdict.resolution_contextually_sound:
                llm_stats["resolution_contextually_sound"] += 1
            pair_record["llm_verdict"] = verdict.model_dump()

        pairs_out.append(pair_record)

    return ResolutionEvalResult(
        checkpoint_transcript_id="",
        reviewed=False,
        labelled_by="",
        n_gold=len(matches),
        n_matched=sum(1 for m in matches if m.pred is not None),
        status_correct=status_correct,
        status_total=status_total,
        resolved_at_correct=resolved_at_correct,
        resolved_at_total=resolved_at_total,
        evidence_locate_hits=evidence_locate_hits,
        evidence_locate_total=evidence_locate_total,
        evidence_overlap_hits=evidence_overlap_hits,
        evidence_overlap_total=evidence_overlap_total,
        revised_link_correct=revised_link_correct,
        revised_link_total=revised_link_total,
        false_open=false_open,
        false_close=false_close,
        confusion=confusion,
        llm_stats=llm_stats,
        pairs=pairs_out,
    )


# --------------------- public API ---------------------


def run_resolution_eval(
    *,
    gold_resolution_path: Path = Path("data/eval/gold/resolution/checkpoint_after_04.json"),
    gold_extraction_dir: Path = Path("data/eval/gold/extraction"),
    predictions_path: Path = Path(
        "data/claims/steps/04_2022-02-10_earnings_call_Q4/claims_with_resolutions.json"
    ),
    parsed_dir: Path = Path("data/parsed"),
    similarity: float = 0.88,
) -> ResolutionEvalResult:
    gold_resolution = load_resolution_checkpoint(gold_resolution_path)
    gold_extractions = load_extraction_gold_dir(gold_extraction_dir)
    extraction_index = _build_gold_extraction_index(gold_extractions)

    gold_rows = _gold_rows_with_full_quote(gold_resolution, extraction_index)
    preds = _load_predictions(predictions_path)

    matches, _ = match_predictions_to_gold(gold_rows, preds, similarity=similarity)

    result = _evaluate_pairs(
        matches,
        extraction_index,
        parsed_dir,
        similarity=similarity,
    )
    result.checkpoint_transcript_id = gold_resolution.checkpoint_transcript_id
    result.reviewed = gold_resolution.is_user_reviewed
    result.labelled_by = gold_resolution.labelled_by
    return result


def _pct(num: int, denom: int) -> str:
    if denom <= 0:
        return "  n/a"
    return f"{(num / denom) * 100:5.1f}%"


def format_resolution_report(report: ResolutionEvalResult, *, similarity: float) -> str:
    del similarity
    lines: list[str] = [
        "RESOLUTION AGENT EVAL",
        "=" * 50,
        "Gold: data/eval/gold/resolution/checkpoint_after_04.json",
        "Predictions: data/claims/steps/04_.../claims_with_resolutions.json",
        "",
        f"  Gold resolutions: {report.n_gold}",
        f"  Matched to pipeline: {report.n_matched}",
        f"  Recall (pairing): {report.n_matched / report.n_gold * 100:.1f}%"
        if report.n_gold
        else "  Recall: n/a",
        f"  Status exact match: {report.status_correct}/{report.status_total}",
        "",
    ]
    judged = report.llm_stats.get("judged", 0)
    if judged:
        lines.append("  LLM judge (on matched pairs):")
        for key, label in (
            ("reproduces_gold_status", "reproduces gold status"),
            ("evidence_relevant", "evidence relevant"),
            ("resolution_contextually_sound", "resolution sound"),
        ):
            lines.append(f"    {label}: {report.llm_stats[key]}/{judged}")
    return "\n".join(lines)


def write_resolution_report(
    report: ResolutionEvalResult,
    *,
    similarity: float,
    report_path: Path = Path("data/stats/resolution_eval_report.txt"),
    results_path: Path = Path("data/stats/resolution_eval_results.json"),
) -> tuple[Path, Path]:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        format_resolution_report(report, similarity=similarity), encoding="utf-8"
    )

    payload = {
        "similarity": similarity,
        "eval_method": "llm_judge",
        "checkpoint_transcript_id": report.checkpoint_transcript_id,
        "reviewed": report.reviewed,
        "labelled_by": report.labelled_by,
        "n_gold": report.n_gold,
        "n_matched": report.n_matched,
        "status_accuracy": report.status_accuracy,
        "resolved_at_accuracy": report.resolved_at_accuracy,
        "evidence_locate_pct": report.evidence_locate_pct,
        "evidence_overlap_pct": report.evidence_overlap_pct,
        "revised_link_pct": report.revised_link_pct,
        "false_open": report.false_open,
        "false_close": report.false_close,
        "confusion": report.confusion,
        "llm_stats": report.llm_stats,
        "pairs": report.pairs,
    }
    results_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return report_path, results_path
