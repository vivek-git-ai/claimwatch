"""Map corpus resolution statuses to take-home PDF outcome language."""

from __future__ import annotations

# Internal status (pipeline) → PDF / interview outcome (brief wording)
CORPUS_TO_PDF_OUTCOME: dict[str, str] = {
    "confirmed": "Materialized",
    "partial": "Partially materialized",
    "failed": "Didn't materialize",
    "unresolvable": "Remains unresolvable",
    "open": "Pending (walk-forward)",
    "revised": "Revised (superseded)",
    "stale": "Quietly dropped (stale)",
    "n/a": "Not applicable",
}

PDF_OUTCOME_DESCRIPTIONS: dict[str, str] = {
    "Materialized": "Later in-corpus evidence supports the claim (management treated it as achieved).",
    "Partially materialized": "Partly met, weakened, or only partially delivered.",
    "Didn't materialize": "Contradicted, abandoned, or clearly not happening.",
    "Remains unresolvable": "Never testable from transcripts alone.",
    "Pending (walk-forward)": "Not yet resolved at the simulated “as-of” point; may still close later.",
    "Revised (superseded)": "Target or timeline changed; successor claim in same thread.",
    "Quietly dropped (stale)": "Horizon passed with no explicit resolution (auto-stale after grace).",
    "Not applicable": "Edge / non-resolution case.",
}

# Four outcomes named explicitly in the take-home brief
PDF_CORE_OUTCOMES = (
    "Materialized",
    "Partially materialized",
    "Didn't materialize",
    "Remains unresolvable",
)


def pdf_outcome_label(corpus_status: str) -> str:
    """PDF-style outcome label for a corpus status."""
    return CORPUS_TO_PDF_OUTCOME.get(corpus_status, corpus_status.replace("_", " ").title())


def format_status_display(corpus_status: str, *, include_internal: bool = True) -> str:
    """Human-readable label for UI (PDF term + optional internal code)."""
    pdf = pdf_outcome_label(corpus_status)
    if not include_internal or pdf == corpus_status:
        return pdf
    return f"{pdf} ({corpus_status})"


def mapping_table_rows() -> list[dict[str, str]]:
    """Rows for docs / dashboard legend."""
    return [
        {
            "corpus_status": k,
            "pdf_outcome": v,
            "notes": PDF_OUTCOME_DESCRIPTIONS.get(v, ""),
        }
        for k, v in CORPUS_TO_PDF_OUTCOME.items()
    ]
