# How to improve ClaimWatch

Prioritised ideas based on current gaps and user feedback.

**In place:** Walk-forward pipeline, committed `data/claims/`, golden-set eval, Streamlit dashboard, **Ask** (Weaviate + Azure embeddings).

## High impact — trust and quality

### 1. Extraction deduplication and subject cleanup

- Merge near-duplicate claims (same quote/thread, different IDs).
- Normalise `subject` from `timeframe` + `date_made`, not free-form LLM subject alone.
- Post-pass validator: reject claims where subject year ≠ meeting year unless explicitly comparative.

### 2. Resolution against external outcomes (optional mode)

- In-corpus resolution stays default; optional pass compares quantitative claims to reported metrics from filings.
- Clearly label `confirmed_external` vs `confirmed_in_transcript`.

### 3. Expand golden-set coverage

- Extend gold beyond transcripts `00`–`04`.
- Add `check` CLI for per-transcript sanity (claim count, quote locate rate).

### 4. Human review hooks

- Export “needs review” CSV; dashboard edit overrides (re-export JSON).

## High impact — exploration

### 5. Corpus search (partially done)

- **Done:** Ask semantic search over threads via Weaviate (`ClaimThread`).
- **Next:** Hybrid FTS + vector; claim-level (not just thread) index; Explorer search box.

### 6. Pipeline cost report

- End of `run --all`: total USD/tokens by extract vs resolve → `data/stats/cost_summary.txt`.

## Pipeline and data

### 7. Incremental runs

- `run --from <transcript_id>` — append from mid-corpus without full rerun.

### 8. Thread merge rules

- Automatic merge of threads with same subject + speaker within N days.

## Deploy and ops

### 9. CI eval job

- GitHub Action: `run --limit 5` + `eval`; warn on F1 regression.

### 10. Auth in front of dashboard

- Reverse proxy or Streamlit auth for non-public deploys.

## Maintainer refresh

```powershell
uv run python -m src.main run --all
uv run python -m src.main rebuild-trace
uv run python -m src.main eval
git add data/claims/ data/eval/results.json
```
