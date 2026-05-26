"""Build data/eval/results.json — one file for both agent evals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.eval.extraction_eval import ExtractionEvalReport, TranscriptExtractionResult
from src.eval.resolution_eval import ResolutionEvalResult

RESULTS_PATH = Path("data/eval/results.json")
RESULTS_TXT_PATH = Path("data/eval/results.txt")


def _pct(good: int, total: int) -> float | None:
    return round((good / total) * 100, 1) if total else None


def _score(good: int, total: int, label: str) -> dict[str, Any]:
    return {
        "label": label,
        "good": good,
        "total": total,
        "accuracy_pct": _pct(good, total),
    }


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "precision_pct": round(prec * 100, 1),
        "recall_pct": round(rec * 100, 1),
        "f1_pct": round(f1 * 100, 1),
        "summary": f"reproduced {tp} gold claims; {fp} extra predictions; missed {fn} gold",
    }


def _extraction_llm(transcripts: list[TranscriptExtractionResult]) -> dict[str, Any]:
    judged = sum(t.llm_stats.get("judged", 0) for t in transcripts)
    repro = sum(t.llm_stats.get("reproduces_gold", 0) for t in transcripts)
    ctx = sum(t.llm_stats.get("contextually_relevant", 0) for t in transcripts)
    quote = sum(t.llm_stats.get("quote_supported", 0) for t in transcripts)
    all_pass = 0
    for t in transcripts:
        for p in t.pairs:
            v = p.get("llm_verdict")
            if v and v.get("reproduces_gold") and v.get("contextually_relevant") and v.get(
                "quote_supported"
            ):
                all_pass += 1
    return {
        "reproduces_gold": _score(repro, judged, "Same claim as gold"),
        "contextually_relevant": _score(ctx, judged, "Valid forward-looking management claim"),
        "quote_supported": _score(quote, judged, "Quote supports paraphrase"),
        "all_criteria_pass": _score(all_pass, judged, "All three pass"),
    }


def _resolution_llm(result: ResolutionEvalResult) -> dict[str, Any]:
    judged = result.llm_stats.get("judged", 0)
    status = result.llm_stats.get("reproduces_gold_status", 0)
    evidence = result.llm_stats.get("evidence_relevant", 0)
    sound = result.llm_stats.get("resolution_contextually_sound", 0)
    all_pass = 0
    for p in result.pairs:
        v = p.get("llm_verdict")
        if v and v.get("reproduces_gold_status") and v.get("evidence_relevant") and v.get(
            "resolution_contextually_sound"
        ):
            all_pass += 1
    return {
        "reproduces_gold_status": _score(status, judged, "Status matches gold"),
        "evidence_relevant": _score(evidence, judged, "Evidence supports status"),
        "resolution_contextually_sound": _score(sound, judged, "Overall resolution sound"),
        "all_criteria_pass": _score(all_pass, judged, "All three pass"),
    }


def build_eval_results(
    extraction: ExtractionEvalReport,
    resolution: ResolutionEvalResult,
    *,
    similarity: float = 0.88,
    predictions_path: str = "data/claims/claims_made.json",
    resolution_predictions_path: str = "",
) -> dict[str, Any]:
    tp, fp, fn = extraction.total_tp, extraction.total_fp, extraction.total_fn
    n_gold = sum(t.n_gold for t in extraction.transcripts)
    n_pred = sum(t.n_pred for t in extraction.transcripts)

    res_tp = resolution.n_matched
    res_fn = resolution.n_gold - resolution.n_matched

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gold_dir": "data/eval/gold",
        "predictions": {
            "extraction": predictions_path,
            "resolution": resolution_predictions_path
            or "data/claims/steps/04_2022-02-10_earnings_call_Q4/claims_with_resolutions.json",
        },
        "matcher_threshold": similarity,
        "extraction_agent": {
            "description": "Claim extractor — how many golden claims did the pipeline reproduce?",
            "gold_total": n_gold,
            "predicted_total": n_pred,
            "reproduction": _prf(tp, fp, fn),
            "llm_judge_on_matched_pairs": _extraction_llm(extraction.transcripts),
            "by_transcript": [
                {
                    "transcript_id": t.transcript_id,
                    "gold_total": t.n_gold,
                    "predicted_total": t.n_pred,
                    "reproduction": _prf(t.tp, t.fp, t.fn),
                    "llm_judge": _extraction_llm([t]),
                }
                for t in extraction.transcripts
            ],
        },
        "resolution_agent": {
            "description": "Claim resolver — how many golden resolutions did the pipeline reproduce?",
            "gold_total": resolution.n_gold,
            "checkpoint": resolution.checkpoint_transcript_id,
            "reproduction": {
                "true_positives": res_tp,
                "false_negatives": res_fn,
                "recall": round(res_tp / resolution.n_gold, 4) if resolution.n_gold else 0,
                "recall_pct": _pct(res_tp, resolution.n_gold),
                "summary": f"matched {res_tp}/{resolution.n_gold} gold resolution rows",
            },
            "status_exact_match": _score(
                resolution.status_correct,
                resolution.status_total,
                "Predicted status equals gold (deterministic)",
            ),
            "llm_judge_on_matched_pairs": _resolution_llm(resolution),
        },
    }


def write_eval_results(
    results: dict[str, Any],
    *,
    json_path: Path = RESULTS_PATH,
    txt_path: Path = RESULTS_TXT_PATH,
) -> tuple[Path, Path]:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    txt_path.write_text(format_eval_results(results), encoding="utf-8")
    return json_path, txt_path


def format_eval_results(results: dict[str, Any]) -> str:
    e = results["extraction_agent"]
    r = results["resolution_agent"]
    er = e["reproduction"]
    el = e["llm_judge_on_matched_pairs"]
    rl = r["llm_judge_on_matched_pairs"]
    lines = [
        "CLAIMWATCH EVAL (gold: data/eval  vs  pipeline: data/claims)",
        "=" * 60,
        "",
        "1. EXTRACTION AGENT",
        f"   Gold claims: {e['gold_total']}   Pipeline claims: {e['predicted_total']}",
        f"   Reproduced (matched): {er['true_positives']}",
        f"   Precision: {er['precision_pct']}%  ({er['true_positives']}/{er['true_positives']+er['false_positives']} predictions in gold)",
        f"   Recall:    {er['recall_pct']}%  ({er['true_positives']}/{e['gold_total']} gold found)",
        f"   F1:        {er['f1_pct']}%",
        "",
        "   LLM judge on matched pairs:",
    ]
    for key in ("reproduces_gold", "contextually_relevant", "quote_supported", "all_criteria_pass"):
        m = el[key]
        lines.append(f"     {m['label']}: {m['good']}/{m['total']} ({m['accuracy_pct']}%)")
    lines.extend(
        [
            "",
            "2. RESOLUTION AGENT",
            f"   Gold resolutions: {r['gold_total']}   Matched: {r['reproduction']['true_positives']}",
            f"   Recall (pairing): {r['reproduction']['recall_pct']}%",
            "",
            "   LLM judge on matched pairs:",
        ]
    )
    for key in (
        "reproduces_gold_status",
        "evidence_relevant",
        "resolution_contextually_sound",
        "all_criteria_pass",
    ):
        m = rl[key]
        lines.append(f"     {m['label']}: {m['good']}/{m['total']} ({m['accuracy_pct']}%)")
    lines.append("")
    lines.append(f"Full JSON: {RESULTS_PATH}")
    return "\n".join(lines)
