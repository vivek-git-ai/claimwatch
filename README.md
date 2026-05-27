# ClaimWatch

Walk-forward tracking of forward-looking claims from Rockwool International earnings transcripts (2021–2026). Extract claims chronologically, resolve them against later transcripts, explore results in a Streamlit dashboard, and measure quality against a golden set.

## Quick start

```powershell
cd claimwatch
uv sync
copy .env.example .env
```

Edit `.env` with your **Azure OpenAI** endpoint and API key (used for extraction, resolution, and eval).

### Dashboard (explore committed corpus)

```powershell
uv run python -m src.main dashboard
```

Open **http://localhost:8501** — Explorer, threads, resolution analytics, pipeline eval, and **Ask** (semantic Q&A). Browse pages use **`data/claims/`** JSON; **Ask** also needs Weaviate + Azure embedding keys (see `.env.example`).

After pulling corpus changes, refresh the Ask index:

```powershell
uv run python -m src.main index-threads --reset
```

### Rebuild corpus (maintainers)

```powershell
uv run python -m src.main parse --all
uv run python -m src.main run --all
uv run python -m src.main rebuild-trace
uv run python -m src.main eval         # optional: refresh data/eval/results.json
```

Commit updated `data/claims/` when the corpus changes.

## Main CLI commands

| Command | Purpose |
|---------|---------|
| `parse --all` | PDF → structured JSON in `data/parsed/` |
| `run --all` | Walk-forward extraction + resolution |
| `rebuild-trace` | Rebuild `claims_threads.json` traces from steps |
| `dashboard` | Streamlit analytics UI |
| `index-threads` | Embed threads → Weaviate (`--reset`, `--clean-all`) |
| `ask "…"` | CLI natural-language thread Q&A |
| `status` | Parsed + claim corpus counts |
| `eval` | Golden-set eval → `data/eval/results.json` |
| `eval-extract` / `eval-resolve` | Single eval pass + detailed reports |

## Documentation

| Document | Contents |
|----------|----------|
| [docs/taxonomy.md](docs/taxonomy.md) | PDF outcome mapping, extraction & hedging |
| [docs/architecture.md](docs/architecture.md) | System design |
| [docs/data-and-parsing.md](docs/data-and-parsing.md) | Data flow, parsing, corpus files |
| [docs/evaluation.md](docs/evaluation.md) | Golden-set eval |
| [docs/tradeoffs.md](docs/tradeoffs.md) | Design choices |
| [docs/improvements.md](docs/improvements.md) | Roadmap |

## Project layout

```
claimwatch/
  app/dashboard.py
  src/                      # ingestion, agents, pipeline, eval
  data/claims/              # Pipeline JSON (Explorer, threads, eval)
  data/parsed/              # Parsed transcripts
  data/eval/                # Gold labels + eval results
```

## License / data

Transcript PDFs are FactSet CallStreet materials — use according to your organisation’s licence. Do not commit `.env` or API keys.
