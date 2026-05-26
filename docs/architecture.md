# ClaimWatch architecture

## Overview

ClaimWatch has four concerns:

1. **Offline pipeline** — Process transcripts in chronological order; extract forward-looking claims; resolve prior open claims using only later in-corpus evidence.
2. **Corpus store** — Versioned JSON (claims, threads, step snapshots) committed to git.
3. **Online interface** — Streamlit dashboard over JSON (Explorer, threads, resolution analytics, pipeline eval).
4. **Quality eval** (optional) — Golden labels in `data/eval/gold/` compared to pipeline output; consolidated in `data/eval/results.json`.

```mermaid
flowchart TB
  subgraph sources [Sources]
    PDF[FactSet PDF transcripts]
  end

  subgraph offline [Offline pipeline]
    Parse[pdf_parser + metadata]
    Orch[Walk-forward orchestrator]
    Ext[Claim extractor LLM]
    Res[Claim resolver LLM]
    Store[corpus_store JSON export]
    Parse --> Orch
    Orch --> Ext
    Orch --> Res
    Ext --> Store
    Res --> Store
  end

  subgraph storage [Storage]
    JSON[data/claims/*.json]
    Steps[data/claims/steps/]
    Eval[data/eval/]
  end

  subgraph online [Online]
    Dash[Streamlit dashboard]
    JSON --> Dash
    Eval --> Dash
  end

  PDF --> Parse
  Store --> JSON
```

## Walk-forward orchestrator

For each transcript **T** in date order:

| Step | Action |
|------|--------|
| 0 | **Auto-stale** — Open claims past `timeframe + 120 days` grace → `stale` |
| 1 | **Pass 1 — Extract** — New atomic claims from management discussion + Q&A (LLM) |
| 2 | **Pass 2 — Resolve** — Only claims that were open *before* T; resolver does not invent new claims; new claims from T can appear in `resolved_by` |
| 3 | **Snapshot** — Write per-transcript step under `data/claims/steps/<transcript_id>/` |
| 4 | **Export** — Rolling `claims_made.json`, `claims_with_resolutions.json`, `claims_threads.json` |

Resolution is **in-corpus only**: evidence must appear in a later transcript in the processed set, not external financials.

## Dashboard pages

| Page | Data source |
|------|-------------|
| Overview | JSON corpus aggregates |
| Pipeline Eval | `data/eval/results.json` |
| Claims Explorer | `claims_with_resolutions.json` |
| Claim Detail | Claim + PDF citation + transcript excerpt |
| Threads | `claims_threads.json` + trace |
| Resolution Analytics | Status timelines, time-to-resolve |
| Speakers | Speaker-level stats |

## Golden-set evaluation

See [evaluation.md](evaluation.md). Commands: `eval`, `eval-extract`, `eval-resolve`.

## Key modules

| Path | Role |
|------|------|
| `src/ingestion/pdf_parser.py` | FactSet PDF → speaker turns |
| `src/ingestion/parsed_loader.py` | Load `data/parsed/*.json` |
| `src/llm/azure.py` | Azure OpenAI structured chat (extract, resolve, eval judge) |
| `src/agents/claim_extractor.py` | Pass 1 structured extraction |
| `src/agents/claim_resolver.py` | Pass 2 resolution |
| `src/pipeline/orchestrator.py` | Chronological loop |
| `src/pipeline/claim_trace.py` | Thread traces from step diffs |
| `src/eval/*` | Gold matching + LLM judge |
| `app/dashboard.py` | Streamlit UI |
