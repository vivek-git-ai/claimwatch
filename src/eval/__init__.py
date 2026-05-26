"""Golden-set evaluation for claim extraction and resolution.

Matcher pairs gold rows to pipeline predictions (quote + paraphrase fuzzy match).
LLM judge (Azure GPT-4o) scores every matched pair — primary quality signal.

Consolidated dashboard output: `data/eval/results.json` via `eval`.
See `docs/evaluation.md` for methodology.
"""

from src.eval.gold_loader import (
    GoldExtractionFile,
    GoldExtractionClaim,
    GoldResolutionFile,
    GoldResolutionClaim,
    load_extraction_gold_dir,
    load_extraction_gold_file,
    load_resolution_checkpoint,
)
from src.eval.matcher import MatchPair, match_predictions_to_gold

__all__ = [
    "GoldExtractionFile",
    "GoldExtractionClaim",
    "GoldResolutionFile",
    "GoldResolutionClaim",
    "MatchPair",
    "load_extraction_gold_dir",
    "load_extraction_gold_file",
    "load_resolution_checkpoint",
    "match_predictions_to_gold",
]
