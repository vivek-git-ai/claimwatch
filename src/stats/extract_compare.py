"""Compare two extraction runs (folder of data/stats/extractions/*.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path


@dataclass
class ClaimDiff:
    transcript_id: str
    only_in_a: list[str] = field(default_factory=list)
    only_in_b: list[str] = field(default_factory=list)
    label_changes: list[str] = field(default_factory=list)  # same claim, different type/subtype/hedging


def _load_claims(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("claims", [])


def _claim_key(c: dict) -> str:
    return (c.get("normalized_claim") or "").strip().lower()


def _labels(c: dict) -> str:
    return (
        f"type={c.get('claim_type')} subtype={c.get('claim_subtype')} "
        f"hedging={c.get('hedging_level')} horizon={c.get('time_horizon')}"
    )


def _match_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def compare_extraction_dirs(dir_a: Path, dir_b: Path, *, similarity: float = 0.88) -> list[ClaimDiff]:
    """Compare matching transcript JSON files in two directories."""
    files_a = {p.stem: p for p in dir_a.glob("*.json")}
    files_b = {p.stem: p for p in dir_b.glob("*.json")}
    all_ids = sorted(set(files_a) | set(files_b))

    results: list[ClaimDiff] = []
    for tid in all_ids:
        diff = ClaimDiff(transcript_id=tid)
        if tid not in files_a:
            diff.only_in_b.append(f"(entire file missing in A)")
            results.append(diff)
            continue
        if tid not in files_b:
            diff.only_in_a.append(f"(entire file missing in B)")
            results.append(diff)
            continue

        claims_a = _load_claims(files_a[tid])
        claims_b = _load_claims(files_b[tid])

        matched_b: set[int] = set()
        for ca in claims_a:
            key_a = _claim_key(ca)
            best_j = -1
            best_score = 0.0
            for j, cb in enumerate(claims_b):
                if j in matched_b:
                    continue
                score = _match_score(key_a, _claim_key(cb))
                if score > best_score:
                    best_score = score
                    best_j = j

            if best_j < 0 or best_score < similarity:
                diff.only_in_a.append(ca.get("normalized_claim", "")[:120])
                continue

            matched_b.add(best_j)
            cb = claims_b[best_j]
            if _labels(ca) != _labels(cb):
                diff.label_changes.append(
                    f"A: {ca.get('normalized_claim', '')[:80]}...\n"
                    f"   A labels: {_labels(ca)}\n"
                    f"   B labels: {_labels(cb)}"
                )

        for j, cb in enumerate(claims_b):
            if j not in matched_b:
                diff.only_in_b.append(cb.get("normalized_claim", "")[:120])

        results.append(diff)

    return results


def format_diff_report(diffs: list[ClaimDiff], dir_a: Path, dir_b: Path) -> str:
    lines = [
        f"Compare extraction runs",
        f"  A: {dir_a}",
        f"  B: {dir_b}",
        "",
    ]
    total_only_a = total_only_b = total_label = 0
    for d in diffs:
        if not d.only_in_a and not d.only_in_b and not d.label_changes:
            lines.append(f"[{d.transcript_id}] IDENTICAL (matched claims)")
            continue
        lines.append(f"[{d.transcript_id}]")
        if d.only_in_a:
            total_only_a += len(d.only_in_a)
            lines.append(f"  Only in A ({len(d.only_in_a)}):")
            for x in d.only_in_a:
                lines.append(f"    - {x}")
        if d.only_in_b:
            total_only_b += len(d.only_in_b)
            lines.append(f"  Only in B ({len(d.only_in_b)}):")
            for x in d.only_in_b:
                lines.append(f"    - {x}")
        if d.label_changes:
            total_label += len(d.label_changes)
            lines.append(f"  Label changes ({len(d.label_changes)}):")
            for x in d.label_changes:
                lines.append(f"    * {x}")
        lines.append("")

    lines.append(
        f"SUMMARY: only_in_a={total_only_a}  only_in_b={total_only_b}  label_changes={total_label}"
    )
    return "\n".join(lines)
