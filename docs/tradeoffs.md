# Design tradeoffs

## Walk-forward vs batch extraction

**Choice:** Process transcripts strictly in chronological order; resolve only against the past.

**Why:** Mimics what an analyst would have known at each meeting; avoids hindsight from future calls when judging early claims.

**Cost:** Cannot resolve a 2021 claim using evidence only stated in 2020; resolution is **in-corpus** by design. External actuals (10-K, market data) are out of scope.

## In-corpus resolution only

**Choice:** Resolver sees later transcript text, not reported financial outcomes.

**Why:** Keeps the system self-contained on public spoken guidance; evidence quotes are auditable in PDFs.

**Cost:** A claim can be `confirmed` with qualitative management language, not independent verification against realised numbers. Some “confirmed” labels mean “management said it happened,” not “analyst verified.”

## LLM extraction and resolution

**Choice:** GPT-4o-mini (configurable) for extract/resolve; structured Pydantic outputs.

**Why:** Qualitative claims need semantic judgment; rules alone miss nuance and hedge language.

**Cost:** Non-deterministic; duplicate or near-duplicate claims (e.g. same quote twice); occasional wrong `subject` / year labels. Extraction quality depends on prompts and model.

## Auto-stale (120-day grace)

**Choice:** Open claims past parseable horizon + 120 days → `stale` without explicit contradiction.

**Why:** Prevents infinite “open” backlog when management never revisits a topic.

**Cost:** May mark claims stale when evidence was implicit or in a different wording. Grace period is a blunt instrument.

## JSON-only analytics UI

**Choice:** Dashboard reads committed `data/claims/` JSON only — no vector database at query time.

**Why:** One source of truth; simple teammate setup (`git pull` + dashboard); charts and filters use the same files as eval.

**Cost:** No natural-language “Ask” over the corpus in the app; exploration is filter/table/trace based. Full-text search would require a new feature (local SQLite, embedded search, or restored vector index).

## Subject line vs meeting date

**Choice:** Search and display use `date_made` and `timeframe`; prompts warn that `subject` can mislead.

**Why:** Extractor sometimes labels subject “FY2021” on a 2025 utterance.

**Cost:** Users must use year filters or precise wording in Explorer until extraction improves.

## PDFs not in git

**Choice:** Transcript PDFs live under `docs/transcripts/` locally, not committed.

**Why:** PDFs are large and licence-sensitive.

**Cost:** PDF page previews and quote highlighting in the dashboard need a local transcript mount.

## Management + Q&A both extracted

**Choice:** Claims from CEO/CFO prepared remarks and from Q&A answers.

**Why:** Material forward-looking statements appear in both; analysts care about answers to hard questions.

**Cost:** More claims, more noise; Q&A claims harder to resolve (shorter, context-dependent).

## Golden-set eval vs production review gate

**Choice:** A hand-curated pilot gold set (`data/eval/gold/`, transcripts `00`–`04`) plus `eval` / dashboard **Pipeline Eval** for regression; no edit-and-approve workflow on live claims.

**Why:** Measurable quality on a small window without blocking the pipeline on human review.

**Cost:** Gold is partial (5 transcripts), mostly LLM-labelled with optional human `reviewed=true`; production would still need review queues and edit overrides. See [evaluation.md](evaluation.md).
