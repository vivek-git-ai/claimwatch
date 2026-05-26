# Golden-set evaluation

Two passes, one gold tree, **cross-model triangulation**:

| Pass | Question | Gold | CLI |
|------|----------|------|-----|
| **1. Extraction** | Did the pipeline find the right forward-looking management claims in each transcript? | [`data/eval/gold/extraction/<id>.json`](../data/eval/gold/extraction/) | `eval-extract` |
| **2. Resolution** | After walk-forward through transcript `04`, did the pipeline assign the right final status and evidence to those claims? | [`data/eval/gold/resolution/checkpoint_after_04.json`](../data/eval/gold/resolution/checkpoint_after_04.json) | `eval-resolve` |

Scope: transcripts `00`–`04` (Q1 2021 → Q4 2021 earnings call window). Pilot intentionally small so the gold can be hand-curated.

---

## Cross-model triangulation (why this is a defensible eval)

| Role | Model | Inputs it is allowed to see |
|------|-------|-----------------------------|
| **System under test (SUT)** | Azure GPT-4o ([`src/agents/claim_extractor.py`](../src/agents/claim_extractor.py), [`src/agents/claim_resolver.py`](../src/agents/claim_resolver.py)) | `data/parsed/` only |
| **Gold labeller** | Claude (this Cursor agent), Sonnet family | `data/parsed/` only — **never** reads `data/claims/` during labelling |
| **Stage B judge** | Azure GPT-4o ([`src/eval/llm_judge.py`](../src/eval/llm_judge.py)) | Each matched (gold, pred) pair + parsed source for the quote |
| **Reviewer** | You | Spot-checks both gold sets, edits where you disagree, flips `reviewed=true` |

**Different model families** (Anthropic vs OpenAI) means disagreement on what counts as forward-looking, what's a hedged target, what's revised vs partial, is real signal — not one model rating its own output.

**Bias to disclose** (and we do, in the interview deck):

- Still LLM-vs-LLM. Both are transformers trained on web text; they share some priors (e.g. treating "around 12%" as moderate hedge).
- Stage B judge is the **same family** as SUT (Azure). This is intentional — gives a same-family second opinion alongside the cross-family gold. The deck reports Stage A and Stage B separately so the reader can tell which one drove a number.
- Gold for `00` was hand-written; gold for `01`–`04` was Claude-labelled in this agent (`labelled_by: claude-via-cursor`). The `reviewed=true` flag is the only signal that a human looked at a row.

**Mitigations** baked into the workflow:

- The gold labeller is blocked from `data/claims/` during labelling (manual discipline; we don't have process-level isolation).
- Stable `GOLD-NN-NNN` ids keep extraction gold linked to resolution gold even if you re-extract.
- Reports print **pre-review** numbers and headers state how many gold files have `reviewed=true`. Once you spot-check, re-run and the same report shows post-review numbers.
- Predictions are produced by a separate full pipeline run (`run --limit 5`), not by the eval, so the SUT can't see the gold.

---

## Gold file layout

```
data/eval/gold/
  extraction/
    _template.json
    00_2021-05-20_earnings_call_Q1.json
    01_2021-06-08_esg_meeting.json
    02_2021-08-19_earnings_call_Q2.json
    03_2021-11-25_earnings_call_Q3.json
    04_2022-02-10_earnings_call_Q4.json
  resolution/
    _template.json
    checkpoint_after_04.json
```

### Extraction row (per transcript)

```json
{
  "claim_id": "GOLD-02-007",
  "thread_id": "T-fy2021-topline",
  "source_doc": "02_2021-08-19_earnings_call_Q2",
  "source_section": "mgmt_discussion",
  "date_made": "2021-08-19",
  "speaker": "Jens Birgersson",
  "quote": "verbatim substring from data/parsed/<id>.json",
  "paraphrase": "one-sentence faithful restatement",
  "category": "financial_guidance",
  "subject": "FY2021 top-line growth",
  "timeframe": "FY2021",
  "target_value": "around 20%",
  "hedge_level": "moderate",
  "falsifiable": "Y",
  "notes": null
}
```

### Resolution row (single checkpoint file)

```json
{
  "claim_id": "GOLD-02-007",
  "source_doc": "02_2021-08-19_earnings_call_Q2",
  "quote": "...copied from extraction gold for reference...",
  "paraphrase": "...copied from extraction gold for reference...",
  "expected_status": "revised",
  "expected_resolved_at_transcript": "03_2021-11-25_earnings_call_Q3",
  "expected_evidence_quote": "verbatim from the resolved_at transcript",
  "expected_resolved_by_gold_id": "GOLD-03-002",
  "notes": null
}
```

Status values follow [`docs/taxonomy.md`](taxonomy.md): `confirmed | revised | failed | partial | open | unresolvable | stale | n/a`.

---

## Labelling protocol (what Claude did, on execute)

**For each extraction file `01`–`04`:**

1. Read [`data/parsed/<id>.json`](../data/parsed/) end-to-end (both `mgmt_discussion` and `qa` sections).
2. Apply the rules from [`src/agents/claim_extractor.py`](../src/agents/claim_extractor.py) and [`docs/taxonomy.md`](taxonomy.md):
   - ROCKWOOL **management** only — no analysts, no operators.
   - Forward-looking only — no historical results, no vague macro without a testable hook.
   - **Atomic** — one falsifiable hook per row. A sentence with revenue % + EBIT % + CapEx € → **three** rows.
   - `quote` must be a **verbatim substring** of the parsed transcript text.
   - `paraphrase` stays faithful to hedging; never upgrade "around 12%" to "12%".
3. Where the FactSet PDF tagged the speaker as **Unverified Participant** but content clearly belonged to a named exec (e.g. Anthony Abbotts answering ESG questions about West Virginia), attribute to the inferred exec and add a `notes` field flagging the inference.
4. Write [`data/eval/gold/extraction/<id>.json`](../data/eval/gold/extraction/) with new `GOLD-NN-NNN` ids (zero-padded transcript index + 3-digit sequence).

**For `resolution/checkpoint_after_04.json`:**

1. Load all extraction-gold rows from `00`–`04` (54 claims total in the current gold set).
2. Walk transcripts `01 → 02 → 03 → 04` in date order — this is what the pipeline is supposed to do.
3. For each gold row, decide:
   - `expected_status` per [`docs/taxonomy.md`](taxonomy.md).
   - `expected_resolved_at_transcript` — earliest meeting where the verdict is justified by spoken evidence.
   - `expected_evidence_quote` — **verbatim** substring from that transcript.
   - `expected_resolved_by_gold_id` — successor extraction-gold row id when `revised`.
   - `open` when the horizon hasn't elapsed by `04` (e.g. FY2022 targets, 2030 commitments, 2025 plant volume).
4. Do **not** open [`data/claims/steps/04_…/claims_with_resolutions.json`](../data/claims/steps/04_2022-02-10_earnings_call_Q4/claims_with_resolutions.json) during this work.

---

## Review states

```json
{ "labelled_by": "claude-via-cursor", "reviewed": false, "review_notes": null }
```

After spot-check, edit the file header to:

```json
{
  "labelled_by": "claude-reviewed-by-user",
  "reviewed": true,
  "review_notes": "agreed with all 14 extraction rows; corrected 2 hedge tags on resolution; flipped GOLD-02-007 from confirmed to revised"
}
```

The eval CLIs print a header showing how many gold files have `reviewed=true`. Both pre- and post-review runs go into the interview deck as separate columns.

Light scan depth is enough: ~10 min per extraction file, ~30 min for the resolution checkpoint. The methodology is defensible either way as long as the limitation is disclosed.

---

## Matching (Stage A, deterministic)

Implemented in [`src/eval/matcher.py`](../src/eval/matcher.py).

For each gold row, score every prediction in the candidate pool by:

```
score = 0.5 * sim(quote_g, quote_p) + 0.5 * sim(paraphrase_g, paraphrase_p)
sim   = difflib.SequenceMatcher ratio on lowercased, whitespace-collapsed text
```

A match is accepted when `score ≥ 0.88` (configurable via `--similarity`). Greedy, gold-first; each prediction is consumed at most once.

| Pool restriction | Extraction | Resolution |
|------------------|------------|------------|
| Candidate predictions | Same `source_doc` only | All predictions in the checkpoint snapshot |
| Unmatched gold → | **False Negative** (missed claim) | **False Negative** (gold row has no pred to evaluate) |
| Unmatched pred (extract eval) → | **False Positive** (over-extraction) | n/a |

`source_doc` filter on the extraction side is what makes precision/recall meaningful per transcript instead of being smeared across all 5.

---

## Extraction metrics

### Stage A (deterministic)

| Metric | Definition |
|--------|------------|
| **Precision** | matched / total predictions in that transcript |
| **Recall** | matched / total gold rows in that transcript |
| **F1** | harmonic mean of Precision and Recall |
| **Quote Locate %** | predicted `quote` found as a substring (or ≥85% fuzzy window) inside [`data/parsed/<id>.json`](../data/parsed/) |
| **Over-extraction Rate** | unmatched predictions / total predictions |
| **Tag agreement** | per-tag % of matched pairs where the prediction's `category`, `hedge_level`, `falsifiable` equal gold |

### Stage B (LLM judge — Azure GPT-4o)

For every matched pair, [`src/eval/llm_judge.py`](../src/eval/llm_judge.py) grades the prediction against gold. Same model family as the SUT (Azure) — reported separately from Stage A so readers can see which signal drove a number.

| Verdict field | Question |
|---------------|----------|
| `reproduces_gold` | Does the prediction describe the same atomic claim as gold? |
| `contextually_relevant` | Is it valid forward-looking ROCKWOOL management guidance? |
| `quote_supported` | Does the predicted quote support the predicted paraphrase? |

Aggregated in `data/eval/results.json` as `llm_judge_on_matched_pairs` with **good/total** and **accuracy_pct**. `all_criteria_pass` counts pairs where all three are true.

Every eval run uses the LLM judge on all matcher-matched pairs (no opt-out).

### Outputs

| File | When |
|------|------|
| [`data/eval/results.json`](../data/eval/results.json) | `eval` — **dashboard** (Pipeline Eval page) |
| [`data/eval/results.txt`](../data/eval/results.txt) | `eval` — short text summary |
| [`data/stats/extraction_eval_report.txt`](../data/stats/) | `eval-extract` or `eval` — human-readable detail |
| [`data/stats/extraction_eval_results.json`](../data/stats/) | per-pair JSON (quote locate, tag agreement, LLM verdicts) |

---

## Resolution metrics

### Stage A (deterministic)

| Metric | Definition |
|--------|------------|
| **Status accuracy** | matched pairs where `prediction.status == expected_status` |
| **Resolved-at accuracy** | among non-`open` gold: predicted `resolved_at_transcript == expected_resolved_at_transcript` |
| **Evidence quote locate %** | `expected_evidence_quote` found inside the parsed text of `expected_resolved_at_transcript` (sanity check on gold itself) |
| **Evidence overlap %** | on matched + terminal: `SequenceMatcher` ratio between predicted `evidence_quote` and gold `expected_evidence_quote` ≥ 0.85 |
| **Revised-link accuracy** | gold `revised` rows where the prediction's `resolved_by` thread/claim maps to the matching gold successor (`expected_resolved_by_gold_id`) |
| **False open %** | gold is terminal (`confirmed`/`failed`/`partial`/`revised`) but pred is `open` |
| **False close %** | gold is `open` but pred is terminal |
| **Status confusion matrix** | rows = gold status, cols = pred status |

### Stage B (LLM judge)

| Verdict field | Question |
|---------------|----------|
| `reproduces_gold_status` | Does predicted status match gold (or a defensible alternative)? |
| `evidence_relevant` | Does predicted `evidence_quote` support the predicted status? |
| `resolution_contextually_sound` | Is the overall resolution reasonable at the checkpoint? |

Also reported in `data/eval/results.json` under `resolution_agent.llm_judge_on_matched_pairs`.

### Outputs

| File | When |
|------|------|
| [`data/stats/resolution_eval_report.txt`](../data/stats/) | `eval-resolve` or `eval` |
| [`data/stats/resolution_eval_results.json`](../data/stats/) | per-pair JSON (confusion matrix, LLM verdicts) |

---

## Running the evals

```powershell
# Recommended: extraction + resolution eval + dashboard summary JSON
uv run python -m src.main eval

# Pipeline then eval in one step
uv run python -m src.main run --limit 5 --eval

# Extract-only pipeline, then extraction eval only
uv run python -m src.main run --limit 5 --extract-only --eval-extract

# Individual passes
uv run python -m src.main eval-extract
uv run python -m src.main eval-resolve

# Pilot one transcript
uv run python -m src.main eval-extract --only 00_2021-05-20_earnings_call_Q1
```

Useful flags:

| Flag | Default | Use |
|------|---------|-----|
| `--similarity` | `0.88` | Threshold for the quote+paraphrase fuzzy match (pairing only) |
| `--gold-dir` / `--gold` | `data/eval/gold/...` | Point at a different gold tree |
| `--claims-made` / `--predictions` | `data/claims/...` | Point at a different prediction snapshot |
| `--only <id>` (extract / eval) | all | Run a single transcript |

Predictions come from a real pipeline run:

```powershell
uv run python -m src.main run --limit 5
```

The first 5 transcripts of the corpus are exactly the ones with gold, so no extra filtering is needed.

---

## Dashboard summary JSON

[`data/eval/results.json`](../data/eval/results.json) is written by `eval` (and `run --eval`). The Streamlit **Pipeline Eval** page reads this file only.

Top-level shape:

```json
{
  "generated_at": "2026-05-26T19:07:20+00:00",
  "gold_dir": "data/eval/gold",
  "predictions": {
    "extraction": "data/claims/claims_made.json",
    "resolution": "data/claims/steps/04_2022-02-10_earnings_call_Q4/claims_with_resolutions.json"
  },
  "matcher_threshold": 0.88,
  "extraction_agent": {
    "gold_total": 54,
    "predicted_total": 57,
    "reproduction": {
      "true_positives": 54,
      "false_positives": 3,
      "false_negatives": 0,
      "precision_pct": 94.7,
      "recall_pct": 100.0,
      "f1_pct": 97.3
    },
    "llm_judge_on_matched_pairs": {
      "reproduces_gold": { "good": 53, "total": 54, "accuracy_pct": 98.1, "label": "Same claim as gold" }
    },
    "by_transcript": [ "..." ]
  },
  "resolution_agent": {
    "gold_total": 54,
    "checkpoint": "04_2022-02-10_earnings_call_Q4",
    "reproduction": { "true_positives": 54, "recall_pct": 100.0 },
    "status_exact_match": { "good": 54, "total": 54, "accuracy_pct": 100.0 },
    "llm_judge_on_matched_pairs": { "..." }
  }
}
```

Numbers above are an example from a successful pilot run; re-run `eval` after pipeline or gold changes.

---

## What's deliberately out of scope (v1)

- **Per-step resolution gold.** Only the post-`04` checkpoint. Per-step would multiply labelling by ~5.
- **Full 31-transcript eval.** v2 if we ever need horizon-aware recall on FY2030 claims.
- **Audited financial truth.** Gold is in-corpus spoken evidence only — same constraint as the pipeline. We don't reconcile against ROCKWOOL's reported financials.
- **Inter-rater agreement.** Single labeller (Claude) + single reviewer (you). v2 if multiple reviewers ever participate.
