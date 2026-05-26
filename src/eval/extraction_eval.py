"""Pass 1: extraction evaluation.

Compares claims predicted by the pipeline (Azure GPT-4o, in
`data/claims/claims_made.json`) against gold extraction files
(`data/eval/gold/extraction/<transcript_id>.json`).

Matcher (deterministic pairing only):
  - Greedy quote+paraphrase match to pair gold rows with predictions
  - Precision / Recall / F1 on matched vs missed/extra

LLM judge (primary quality signal on matched pairs):
  - reproduces_gold, contextually_relevant, quote_supported

Outputs:
  - data/stats/extraction_eval_report.txt
  - data/stats/extraction_eval_results.json
  - data/eval/results.json (via `eval`)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.eval.gold_loader import GoldExtractionFile, load_extraction_gold_dir
from src.eval.matcher import MatchPair, match_predictions_to_gold, quote_locate_rate


# ------------------------- types -------------------------


@dataclass
class TranscriptExtractionResult:
    transcript_id: str
    reviewed: bool
    labelled_by: str
    n_gold: int
    n_pred: int
    tp: int
    fp: int
    fn: int
    quote_locate_hits: int
    quote_locate_total: int
    tag_agreement: dict[str, dict[str, int]] = field(default_factory=dict)
    llm_stats: dict[str, int] = field(default_factory=dict)
    pairs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def quote_locate_pct(self) -> float:
        return self.quote_locate_hits / self.quote_locate_total if self.quote_locate_total else 0.0


@dataclass
class ExtractionEvalReport:
    similarity: float
    transcripts: list[TranscriptExtractionResult] = field(default_factory=list)

    @property
    def total_tp(self) -> int:
        return sum(t.tp for t in self.transcripts)

    @property
    def total_fp(self) -> int:
        return sum(t.fp for t in self.transcripts)

    @property
    def total_fn(self) -> int:
        return sum(t.fn for t in self.transcripts)

    @property
    def total_quote_hits(self) -> int:
        return sum(t.quote_locate_hits for t in self.transcripts)

    @property
    def total_quote_attempts(self) -> int:
        return sum(t.quote_locate_total for t in self.transcripts)

    @property
    def precision(self) -> float:
        denom = self.total_tp + self.total_fp
        return self.total_tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.total_tp + self.total_fn
        return self.total_tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


# --------------------- helpers ---------------------


_TAG_FIELDS = ("category", "hedge_level", "falsifiable", "source_section")


def _load_predictions(claims_made_path: Path) -> list[dict[str, Any]]:
    data = json.loads(claims_made_path.read_text(encoding="utf-8"))
    return data.get("claims", [])


def _load_parsed_text(parsed_dir: Path, transcript_id: str) -> str:
    path = parsed_dir / f"{transcript_id}.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    return (data.get("mgmt_discussion_text") or "") + "\n\n" + (data.get("qa_text") or "")


def _eval_single_transcript(
    gold_file: GoldExtractionFile,
    all_predictions: list[dict[str, Any]],
    parsed_dir: Path,
    *,
    similarity: float,
) -> TranscriptExtractionResult:
    preds = [p for p in all_predictions if p.get("source_doc") == gold_file.transcript_id]
    gold_dicts = [c.model_dump() for c in gold_file.claims]
    matches, unmatched_preds = match_predictions_to_gold(
        gold_dicts, preds, similarity=similarity
    )

    parsed_text = _load_parsed_text(parsed_dir, gold_file.transcript_id)

    tp = sum(1 for m in matches if m.pred is not None)
    fn = sum(1 for m in matches if m.pred is None)
    fp = len(unmatched_preds)

    tag_agreement: dict[str, dict[str, int]] = {
        f: {"agree": 0, "total": 0} for f in _TAG_FIELDS
    }

    quote_hits = 0
    quote_attempts = 0
    llm_stats = {
        "reproduces_gold": 0,
        "contextually_relevant": 0,
        "quote_supported": 0,
        "judged": 0,
    }
    pairs_out: list[dict[str, Any]] = []

    from src.eval.llm_judge import judge_extraction_pair

    for m in matches:
        pair_record: dict[str, Any] = {
            "gold_id": m.gold.get("claim_id"),
            "matched": m.pred is not None,
            "score": round(m.score, 4),
            "basis": m.basis,
        }
        if m.pred is not None:
            for field_name in _TAG_FIELDS:
                gold_val = (m.gold.get(field_name) or "").strip().lower()
                pred_val = (m.pred.get(field_name) or "").strip().lower()
                if gold_val and pred_val:
                    tag_agreement[field_name]["total"] += 1
                    if gold_val == pred_val:
                        tag_agreement[field_name]["agree"] += 1

            if parsed_text and m.pred.get("quote"):
                located, score = quote_locate_rate(
                    m.pred.get("quote"), parsed_text, similarity=similarity
                )
                quote_attempts += 1
                if located:
                    quote_hits += 1
                pair_record["quote_located"] = located
                pair_record["quote_locate_score"] = round(score, 4)

            try:
                verdict = judge_extraction_pair(m.gold, m.pred)
            except Exception as exc:  # noqa: BLE001
                pair_record["llm_error"] = str(exc)
            else:
                llm_stats["judged"] += 1
                if verdict.reproduces_gold:
                    llm_stats["reproduces_gold"] += 1
                if verdict.contextually_relevant:
                    llm_stats["contextually_relevant"] += 1
                if verdict.quote_supported:
                    llm_stats["quote_supported"] += 1
                pair_record["llm_verdict"] = verdict.model_dump()

            pair_record["pred_claim_id"] = m.pred.get("claim_id")
        pairs_out.append(pair_record)

    return TranscriptExtractionResult(
        transcript_id=gold_file.transcript_id,
        reviewed=gold_file.is_user_reviewed,
        labelled_by=gold_file.labelled_by,
        n_gold=len(gold_dicts),
        n_pred=len(preds),
        tp=tp,
        fp=fp,
        fn=fn,
        quote_locate_hits=quote_hits,
        quote_locate_total=quote_attempts,
        tag_agreement=tag_agreement,
        llm_stats=llm_stats,
        pairs=pairs_out,
    )


# --------------------- public API ---------------------


def run_extraction_eval(
    *,
    gold_dir: Path = Path("data/eval/gold/extraction"),
    claims_made_path: Path = Path("data/claims/claims_made.json"),
    parsed_dir: Path = Path("data/parsed"),
    similarity: float = 0.88,
    transcript_filter: list[str] | None = None,
) -> ExtractionEvalReport:
    """Run extraction eval and return a structured report (no I/O)."""
    gold_files = load_extraction_gold_dir(gold_dir)
    if transcript_filter:
        wanted = set(transcript_filter)
        gold_files = [g for g in gold_files if g.transcript_id in wanted]

    all_preds = _load_predictions(claims_made_path)

    report = ExtractionEvalReport(similarity=similarity)
    for gf in gold_files:
        report.transcripts.append(
            _eval_single_transcript(
                gf,
                all_preds,
                parsed_dir,
                similarity=similarity,
            )
        )
    return report


def _pct(num: int, denom: int) -> str:
    if denom <= 0:
        return "  n/a"
    return f"{(num / denom) * 100:5.1f}%"


def format_extraction_report(report: ExtractionEvalReport) -> str:
    lines: list[str] = [
        "EXTRACTION AGENT EVAL",
        "=" * 50,
        f"Gold: data/eval/gold/extraction  |  Predictions: data/claims/claims_made.json",
        "",
        "OVERALL",
        f"  Gold claims: {sum(t.n_gold for t in report.transcripts)}",
        f"  Pipeline claims: {sum(t.n_pred for t in report.transcripts)}",
        f"  Reproduced (matched): {report.total_tp}",
        f"  Precision: {report.precision*100:.1f}%  Recall: {report.recall*100:.1f}%  F1: {report.f1*100:.1f}%",
        "",
    ]
    judged = sum(t.llm_stats.get("judged", 0) for t in report.transcripts)
    if judged:
        lines.append("  LLM judge (on matched pairs):")
        for key, label in (
            ("reproduces_gold", "reproduces gold"),
            ("contextually_relevant", "contextually relevant"),
            ("quote_supported", "quote supported"),
        ):
            g = sum(t.llm_stats.get(key, 0) for t in report.transcripts)
            lines.append(f"    {label}: {g}/{judged}")
    lines.append("")
    for t in report.transcripts:
        lines.append(f"[{t.transcript_id}] gold={t.n_gold} pred={t.n_pred} matched={t.tp}")
        lines.append(
            f"  P={t.precision*100:.1f}% R={t.recall*100:.1f}% F1={t.f1*100:.1f}%"
        )
    return "\n".join(lines)


def write_extraction_report(
    report: ExtractionEvalReport,
    *,
    report_path: Path = Path("data/stats/extraction_eval_report.txt"),
    results_path: Path = Path("data/stats/extraction_eval_results.json"),
) -> tuple[Path, Path]:
    """Persist human + machine reports to disk and return their paths."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(format_extraction_report(report), encoding="utf-8")

    payload = {
        "similarity": report.similarity,
        "eval_method": "llm_judge",
        "totals": {
            "tp": report.total_tp,
            "fp": report.total_fp,
            "fn": report.total_fn,
            "precision": report.precision,
            "recall": report.recall,
            "f1": report.f1,
            "quote_locate_hits": report.total_quote_hits,
            "quote_locate_total": report.total_quote_attempts,
        },
        "transcripts": [
            {
                "transcript_id": t.transcript_id,
                "reviewed": t.reviewed,
                "labelled_by": t.labelled_by,
                "n_gold": t.n_gold,
                "n_pred": t.n_pred,
                "tp": t.tp,
                "fp": t.fp,
                "fn": t.fn,
                "precision": t.precision,
                "recall": t.recall,
                "f1": t.f1,
                "quote_locate_hits": t.quote_locate_hits,
                "quote_locate_total": t.quote_locate_total,
                "quote_locate_pct": t.quote_locate_pct,
                "tag_agreement": t.tag_agreement,
                "llm_stats": t.llm_stats,
                "pairs": t.pairs,
            }
            for t in report.transcripts
        ],
    }
    results_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return report_path, results_path
