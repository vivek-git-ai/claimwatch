"""Stage A deterministic matcher: align predicted claims to gold rows.

Match score = max(quote_ratio, paraphrase_ratio) using difflib.SequenceMatcher
against lowercased / whitespace-normalised strings. A pair is accepted if score
>= `similarity` (default 0.88, same threshold as src/stats/extract_compare.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def text_similarity(a: str | None, b: str | None) -> float:
    """Return SequenceMatcher ratio on normalised inputs (0.0 if either side empty)."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def claim_match_score(gold: dict[str, Any], pred: dict[str, Any]) -> tuple[float, str]:
    """Best similarity across (quote, paraphrase). Returns (score, basis)."""
    q = text_similarity(gold.get("quote"), pred.get("quote"))
    p = text_similarity(gold.get("paraphrase"), pred.get("paraphrase"))
    if q >= p:
        return q, "quote"
    return p, "paraphrase"


@dataclass
class MatchPair:
    gold: dict[str, Any]
    pred: dict[str, Any] | None
    score: float
    basis: str  # "quote" | "paraphrase" | "unmatched"


def match_predictions_to_gold(
    gold_claims: list[dict[str, Any]],
    pred_claims: list[dict[str, Any]],
    *,
    similarity: float = 0.88,
) -> tuple[list[MatchPair], list[dict[str, Any]]]:
    """Greedy 1-1 matching of predictions to gold by best (quote/paraphrase) score.

    Returns:
      (matches, unmatched_predictions)
      - matches has one entry per gold claim; `pred` is None if no prediction
        clears `similarity`.
      - unmatched_predictions are the predicted claims not assigned to any gold.
    """
    used_pred_idx: set[int] = set()
    matches: list[MatchPair] = []

    for gc in gold_claims:
        best_i = -1
        best_score = 0.0
        best_basis = "unmatched"
        for i, pc in enumerate(pred_claims):
            if i in used_pred_idx:
                continue
            score, basis = claim_match_score(gc, pc)
            if score > best_score:
                best_score = score
                best_i = i
                best_basis = basis

        if best_i >= 0 and best_score >= similarity:
            used_pred_idx.add(best_i)
            matches.append(
                MatchPair(
                    gold=gc,
                    pred=pred_claims[best_i],
                    score=best_score,
                    basis=best_basis,
                )
            )
        else:
            matches.append(MatchPair(gold=gc, pred=None, score=best_score, basis="unmatched"))

    unmatched_preds = [pc for i, pc in enumerate(pred_claims) if i not in used_pred_idx]
    return matches, unmatched_preds


def quote_locate_rate(
    quote: str | None,
    transcript_text: str | None,
    *,
    similarity: float = 0.88,
) -> tuple[bool, float]:
    """Check whether `quote` can be located in `transcript_text` as a substring
    or as an approximate match (sliding window).

    Returns (located, best_score). `located` is True when the best score >=
    `similarity`. We use a coarse sliding window of words to limit cost.
    """
    if not quote or not transcript_text:
        return False, 0.0

    nq = _norm(quote)
    nt = _norm(transcript_text)
    if not nq or not nt:
        return False, 0.0

    if nq in nt:
        return True, 1.0

    words = nt.split()
    qwords = nq.split()
    if not qwords:
        return False, 0.0
    window = max(1, len(qwords))

    best = 0.0
    step = max(1, window // 4)
    for start in range(0, max(1, len(words) - window + 1), step):
        chunk = " ".join(words[start : start + window])
        score = SequenceMatcher(None, nq, chunk).ratio()
        if score > best:
            best = score
            if best >= 0.995:
                break
    return best >= similarity, best
