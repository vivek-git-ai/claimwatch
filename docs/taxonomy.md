# Claim taxonomy and status mapping

Aligned with the take-home brief: *materialized, partially materialized, didn't, or remains unresolvable*.

## Resolution outcomes (PDF ↔ pipeline)

| Corpus status | PDF / interview label | Meaning |
|---------------|----------------------|---------|
| `confirmed` | **Materialized** | Later transcript evidence supports the claim (in-corpus only; not audited vs reported financials). |
| `partial` | **Partially materialized** | Partly met or materially weakened. |
| `failed` | **Didn't materialize** | Contradicted, abandoned, or clearly not happening. |
| `unresolvable` | **Remains unresolvable** | Not testable from spoken guidance alone. |
| `open` | **Pending (walk-forward)** | Still open in the simulation; not a final PDF outcome. |
| `revised` | **Revised (superseded)** | Guidance changed; see successor claim on thread. |
| `stale` | **Quietly dropped (stale)** | Horizon + grace elapsed without explicit resolution. |
| `n/a` | **Not applicable** | Rare edge case. |

Implementation: `src/analytics/status_mapping.py` — used in the dashboard for labels and tables.

## Forward-looking claim (extraction)

**Who:** ROCKWOOL **management** only (prepared remarks + management answers in Q&A).

**Included:** Guidance, targets, expectations, commitments, plans, dated milestones; partially testable outlooks.

**Excluded:** Pure historical results, vague macro without a testable hook, analyst questions, filler.

**Atomicity:** One falsifiable hook per claim (e.g. revenue %, EBIT %, CapEx € → three claims if one sentence has three numbers).

**Mechanism:** LLM structured extraction (`src/agents/claim_extractor.py`) with walk-forward ordering (no future transcripts at extract time).

## Hedging and falsifiability (extraction tags)

| Field | Values | Role |
|-------|--------|------|
| `hedge_level` (corpus) | `firm`, `moderate`, `soft` | Strength of commitment (from hard / soft / conditional / aspirational at extract). |
| `falsifiable` | `Y`, `partial`, `N` | How testable the claim is; `N` = strategic / hard to verify. |

Prompt rule: do **not** upgrade hedged wording to firm commitments in the stored paraphrase.

## Resolution (walk-forward)

Pass 2 (`claim_resolver.py`) updates prior **open** claims using **only** the new transcript. Verdicts use corpus statuses above; evidence must be a verbatim quote from that transcript.

See also [tradeoffs.md](tradeoffs.md) for limits (in-corpus only, LLM judgment, auto-stale) and [evaluation.md](evaluation.md) for how statuses are scored against the golden set.
