# How to present ClaimWatch (beginning to end)

A spoken walkthrough script for a **~45–60 minute** screen-share (AI Engineer take-home). Adjust pace; skip sections if time is short.

**Before you start:** Dashboard running (`uv run python -m src.main dashboard`), repo open, optional local PDFs in `docs/transcripts/` for Claim Detail.

---

## 1. Open (2–3 minutes)

**Say:**

> “ClaimWatch tracks forward-looking claims that Rockwool management made on earnings calls and related events — about 31 FactSet transcripts from 2021 through 2026. The system processes them **in chronological order**, as if each call arrived live: extract new guidance, then update older open claims using **only** what was said on later calls. Everything is auditable with verbatim quotes. I’ll walk through the pipeline, the data we store, how we evaluate it, and where it breaks.”

**Show (optional):** Sidebar → **Architecture** — one sentence: “This is the map; we’ll walk through each box.”

---

## 2. The business problem (3 minutes)

**Say:**

> “The brief asks for three things: **extract** forward-looking management claims, **track** them across time, and **judge** whether they materialized, partially did, didn’t, or can’t be resolved from speech alone. Analysts care about *who said what, when*, and *what happened next* — with evidence, not a black-box score.”

**Map to your design:**

| Brief | Your answer |
|-------|-------------|
| Materialized | `confirmed` |
| Partially materialized | `partial` |
| Didn’t | `failed` |
| Unresolvable | `unresolvable` |
| Still in flight | `open` (during walk-forward) |
| Guidance changed | `revised` + successor claim |
| Never revisited | `stale` (auto after horizon + 120 days) |

**Show:** Sidebar expander **Outcome mapping** or [taxonomy.md](taxonomy.md).

---

## 2a. Why `stale`? — intuition (say this when they ask)

The brief does not use the word “stale,” but it **does** ask what happens when guidance is **never clearly resolved** — the analyst version is *“quietly dropped.”* We encode that as **`stale`**.

### The problem without `stale`

In a walk-forward run, every extracted claim starts as **`open`**. The resolver only changes status when **this call’s text** gives evidence (confirm, fail, revise, partial, unresolvable). If management **never mentions the topic again**, the claim would stay **`open` forever** — even years after the guidance period ended. That is unrealistic: an analyst would stop treating “FY2021 revenue will grow ~20%” as a live open bet once 2021 is long past.

### Intuition (plain language)

> “**Stale** means: the claim’s **time horizon has passed**, we waited a **grace period** for management to say something on the record, and they **didn’t** confirm, contradict, or revise it. We’re not saying it failed — we’re saying **the story went cold** without a clean resolution in the transcripts.”

That is different from:

| Status | Meaning | Evidence |
|--------|---------|----------|
| **`failed`** | Management **said** it didn’t happen / walked it back | Verbatim quote on a later call |
| **`confirmed`** | Later language **supports** it | Verbatim quote |
| **`revised`** | Target or timeline **changed** | New claim + link |
| **`unresolvable`** | **Can’t be tested** from speech alone (too vague, strategic) | Resolver judgment |
| **`stale`** | **Horizon + grace elapsed**, still `open` | **No** explicit resolution — rule-based |

### How it works in code (Step 0 — before extract each call)

**Not an LLM.** Python in `src/pipeline/claim_staleness.py`:

1. At the **start** of each transcript T (date = `as_of`).
2. For each claim still **`open`**:
3. Parse `timeframe` → an **end date** (e.g. `FY2021` → 2021-12-31, `Q2 2022` → 2022-06-30).
4. If `as_of` is **more than 120 days after** that end date → set status to **`stale`**.
5. Note: `Auto-stale: horizon {timeframe} ended before {as_of}`.

**Default grace: 120 days** (`DEFAULT_GRACE_DAYS`). Tunable in code; not exposed on CLI today.

**No end date → never auto-stale.** Vague horizons like `near-term`, `ongoing`, `multi-year`, `future` are skipped — those claims can stay `open` until the resolver says something else.

### Tiny timeline example

```
2021-05-20  Claim: "FY2021 growth around 20%"  →  open
2021-08-19  Still open (no clear resolution on this call)
2022-05-19  FY2021 ended; +120d grace passed
            → Step 0 marks it STALE (no quote required)
```

**Show in UI:** **Claims Explorer** → filter Status = `stale` → open one row → read `resolution_notes` (“Auto-stale: horizon …”).

**Show in Threads:** e.g. `T-production-ramp-up` — `final_status: stale`, trace shows `status_changed` to stale with auto-stale note.

### Why 120 days grace?

> “Earnings are quarterly; management might revisit a prior-year topic on the **next** call or in the annual report commentary. Grace avoids stale-ing on the first day after Dec 31. **120 days is a blunt default** — a product version might tie grace to event type or make it configurable.”

### What to say in the interview

**One-liner:**

> “**Stale is our quietly-dropped bucket**: deterministic, runs before each extract, closes open claims whose stated horizon plus 120 days is behind us, without inventing failure or success when the transcripts stay silent.”

**If they push on accuracy:**

> “It can **false-stale** — evidence might exist in different wording, or the timeframe parse might be wrong. That’s why we document it as a tradeoff, not as ground truth. **`failed` needs explicit negative evidence; `stale` needs silence after the window.**”

**Link to brief:**

> “The four brief outcomes are materialized / partial / didn’t / unresolvable. **`stale` is how we stop the simulation** when none of those happened in-corpus but the clock ran out — otherwise our open backlog would be meaningless at scale.”

Code: `expire_stale_open_claims()` in [claim_staleness.py](../src/pipeline/claim_staleness.py). More detail: [taxonomy.md](taxonomy.md), [complete-system-guide.md](complete-system-guide.md) §13.

---

## 3. Why not “PDF → LLM” in one shot? (4 minutes)

**Say:**

> “We **parse PDFs first** with rules and pdfplumber. FactSet layout is stable: management section, Q&A, speaker blocks separated by dotted lines. That gives us structured JSON — speaker turns, dates, verbatim text — before any model runs.”

**Reasons (pick two):**

1. **Audit** — Eval checks that quotes are substrings of parsed text.
2. **Cost** — Re-run extract/resolve without re-OCR.
3. **Contract** — Agents read `data/parsed/`, not raw bytes.

**Say:**

> “We don’t use vector embedding chunks for this v1. The natural unit is a **speaker turn**; claims are **atomic** — one falsifiable hook per row.”

**CLI (if asked):** `uv run python -m src.main parse --all` → `data/parsed/`.

---

## 4. Walk-forward pipeline — the core story (10–12 minutes)

**Say:**

> “For **each transcript in date order**, we run four steps. This is the heart of the system.”

Use **Architecture** tab → **Walk-forward pipeline**, or say while showing the diagram:

| Step | Name | One line |
|------|------|----------|
| 0 | Auto-stale | Open claims past timeframe + **120 days** → `stale` (see [§2a](#2a-why-stale--intuition-say-this-when-they-ask)) |
| 1 | Extract | LLM finds **new** management claims (mgmt + Q&A) |
| 2 | Resolve | LLM updates claims that were **open before this call**, using **only this call’s text** |
| 3 | Snapshot | Write `data/claims/steps/<transcript_id>/` |
| 4 | Export | Rolling `claims_made`, `claims_with_resolutions`, `claims_threads` |

**Critical discipline (say clearly):**

> “At transcript T, the resolver does **not** use future calls. Claims **created** on T are not resolved **using** T in the same pass — they start open. That mimics what an analyst would have known that day.”

**In-corpus only:**

> “We do **not** pull 10-Ks or market data. `confirmed` means later **management language** supports it, not that I verified the number in filings.”

**LLM:**

> “Azure **gpt-4o-mini** for extract and resolve; structured Pydantic outputs via `src/llm/azure.py`.”

**Resolver practical limit:**

> “We can have hundreds of open claims; we **pre-filter** to ~35 relevant ones per call (same thread as new claims + keyword overlap) to fit context and cost.”

**Step 0 reminder (10 seconds):**

> “Before extract, **auto-stale** closes the ‘clock ran out, nobody said anything’ cases — [§2a](#2a-why-stale--intuition-say-this-when-they-ask).”

**CLI:** `uv run python -m src.main run --all`

---

## 5. What is a claim? (3 minutes)

**Say:**

> “A **claim** is one atomic forward-looking statement: verbatim **quote**, **paraphrase**, speaker, date, category, subject, timeframe, target, hedge level, falsifiability.”

**Extraction rules (short):**

- **Management only** (prepared remarks + mgmt answers in Q&A).
- **Exclude** past results, vague macro, analyst questions.
- **Split** multi-metric sentences into multiple claims.

**Show:** **Claims Explorer** — filter one status, one speaker.

**Open one row → Claim Detail:**

- Quote + paraphrase  
- Resolution status + evidence quote  
- Optional PDF preview if `docs/transcripts/` is local  

---

## 6. What is a thread? (4 minutes)

**Say:**

> “A **thread** is the **storyline** for one topic over multiple meetings — e.g. West Virginia factory startup. Multiple **claims** (RKW-002, RKW-021, …) can belong to one thread.”

**How threading works (important for questions):**

> “We do **not** run a separate ‘threading LLM.’ The extractor assigns a **subject** label and sometimes a **thread_subject_hint** when management reaffirms prior guidance. Python matches that string to an existing thread or creates `T-{slug}`. The **timeline** (`trace`) is rebuilt **deterministically** from step snapshots — uttered + each status change with evidence.”

**Show:** **Threads** — pick `T-west-virginia-factory-startup` or similar:

- `evolution` — each claim when said  
- `trace` — status changes across calls  

**Honest limit:**

> “If the model uses different subject wording for the same topic, we get duplicate threads — that’s on our improvement list.”

---

## 7. Data on disk (3 minutes)

**Say:**

> “The corpus is **JSON in git** — one source of truth for the dashboard and eval.”

```
data/parsed/           ← 31 structured transcripts
data/claims/
  claims_made.json
  claims_with_resolutions.json
  claims_threads.json
  steps/<id>/          ← point-in-time after each call
data/eval/gold/        ← pilot labels 00–04
data/eval/results.json ← last eval run
```

**Say:**

> “Teammates can `git pull` and run the dashboard **without** Azure keys. Keys are only needed to re-run the pipeline.”

**Show:** `status` in terminal if useful: `uv run python -m src.main status`

---

## 8. Dashboard tour (8–10 minutes)

Suggested order:

1. **Overview** — 31 transcripts, ~373 claims, status mix, timeline.  
2. **Architecture** — interactive map (you may have started here).  
3. **Claims Explorer** — filters, search, outcome column.  
4. **Claim Detail** — one strong example (confirmed with evidence).  
5. **Threads** — one multi-meeting storyline.  
6. **Resolution Analytics** — time-to-resolve, outcomes.  
7. **Speakers** — CEO/CFO concentration.  
8. **Ask** — NL question → Weaviate thread search → timeline answer with claim citations.  
9. **Pipeline Eval** — quality metrics (next section).  
10. **LLM Cost** — ~$0.14 for 31 docs; scale to 30k / 300k / 3M.

**One-liner for data vs Ask:**

> “Walk-forward truth stays in **JSON** on git. **Ask** adds a derived **Weaviate** index for semantic thread lookup — reindex with `index-threads` after corpus changes. Explorer doesn’t need Weaviate.”

**Demo Ask (30s):**

> Open **Ask**, confirm sidebar shows 208/208 threads indexed, ask *‘How did leverage guidance change?’*, show retrieval scores, answer, **View claim** on a citation.”

---

## 9. Evaluation (6–8 minutes)

**Say:**

> “We can’t label 373 claims by hand for the interview. We built a **pilot golden set** on transcripts **00–04** — 54 claims — and measure extraction and resolution separately.”

**Cross-model story:**

| Role | Model |
|------|--------|
| Gold labels | Claude (curated from parsed text only) |
| Pipeline (SUT) | Azure gpt-4o-mini |
| Stage B judge | Azure GPT-4o on matched pairs |

**Stage A — deterministic:**

> “Matcher pairs gold to predictions by fuzzy similarity on **quote + paraphrase** (default threshold 0.88). That gives precision, recall, F1, quote locate rate.”

**Stage B — LLM judge:**

> “On matched pairs only: does the prediction reproduce the gold claim? Is it valid forward-looking guidance? Does the quote support the paraphrase? We report Stage A and B **separately** so you see what drove each number.”

**Show:** **Pipeline Eval** page.

**CLI:** `uv run python -m src.main run --limit 5` then `eval`

**Bias disclosure (say proactively):**

> “It’s still LLM-vs-LLM in places; gold for 00 was hand-written, 01–04 Claude-labelled. `reviewed=true` on gold files means a human spot-checked.”

---

## 10. Design tradeoffs — what we chose and why (5 minutes)

Use this as a checklist when they ask “why?”:

| Choice | Why | Cost |
|--------|-----|------|
| Walk-forward | No hindsight | Can’t use future calls to judge past |
| In-corpus resolution | Auditable quotes | Not verified vs financials |
| LLM extract/resolve | Hedge language, qualitative claims | Non-deterministic, dupes, subject errors |
| Auto-stale 120d | No infinite open backlog | May stale implicit evidence |
| JSON + git | Simple deploy, diffs | No NL search in app |
| Subject-based threads | Stable IDs during simulation | Split/merge errors |
| gpt-4o-mini | Cost | Judge uses GPT-4o for eval |

**Show:** Architecture → click a few nodes in the detail panel if time.

Full detail: [tradeoffs.md](tradeoffs.md), [complete-system-guide.md](complete-system-guide.md).

---

## 11. What fails / limits (3 minutes — builds trust)

**Say proactively:**

1. **Confirmed ≠ true in financials** — management said it happened.  
2. **Thread quality** — depends on consistent `subject` labels.  
3. **Resolver cap** — 35 open claims fed per call; scale needs retrieval.  
4. **Gold covers 5 of 31 transcripts** — rest is unevaluated automatically.  
5. **Duplicate or near-duplicate claims** — same quote, two IDs sometimes.  
6. **Subject year vs meeting year** — use `date_made` / `timeframe`, not subject alone.  
7. **Auto-stale** — may close claims when evidence was implicit or wording differed; vague `timeframe` never stales ([§2a](#2a-why-stale--intuition-say-this-when-they-ask)).

**If they ask “what would you build next?”:**

> “Subject normalization and dedup, expand gold, optional filing check for quantitative claims, incremental `run --from`, persist real token usage, local full-text search.”

See [improvements.md](improvements.md).

---

## 12. Scalability — how this design behaves at 31 → 30k → 3M (8–10 minutes)

This is a common interview question: *“How would your approach scale? Is there a limit?”* Answer in **three layers**: cost, compute/architecture, and product/quality.

**Show:** **LLM Cost** page (numbers) + Architecture (design).

---

### 12.1 What scales **well** today

**Say:**

> “The architecture is deliberately **simple and linear** where it matters for a pilot: one parse per document, two LLM calls per document in walk-forward order, one JSON corpus, one read-only dashboard. There is **no** standing vector database, embedding ingest, or query-time LLM — so operational surface area stays small.”

| Layer | Scales how | Why |
|-------|------------|-----|
| **Parse** | ~O(documents) | Rules + pdfplumber; no LLM; embarrassingly parallel if you shard by PDF |
| **Extract** | ~O(documents) | One structured LLM call per transcript; input ≈ transcript length |
| **Resolve** | ~O(documents × fed_open) | One call per transcript; **fed_open capped at 35** today |
| **Dashboard browse** | O(claims) read | Loads JSON once, caches in memory — fine to ~low millions of rows on a decent machine |
| **Deploy** | Static JSON | Streamlit Cloud / `git pull` — no DB cluster for demo |

**Walk-forward must stay sequential** across time (claim state at T depends on T−1). You **can** parallelize parse, and you **can** shard by **company** or **tenant**, but not by arbitrary transcript order within one issuer’s timeline.

---

### 12.2 Cost scalability (LLM)

**Say:**

> “Money scales about **linearly with document count** if average transcript size and open-claim pressure stay similar to Rockwool earnings calls.”

| Corpus size | Est. pipeline total (gpt-4o-mini heuristic) | Per document |
|-------------|-----------------------------------------------|--------------|
| **31** (this repo) | ~**$0.14** | ~$0.0045 |
| **30,000** | ~**$136** | same slope |
| **300,000** | ~**$1,361** | same slope |
| **3,000,000** | ~**$13,606** | same slope |

**What drives cost per transcript:**

1. **Extract** — tokens ∝ mgmt+Q&A text length (~fixed per call type).  
2. **Resolve** — tokens ∝ transcript text + **(up to 35)** open claims × ~115 tokens each in the prompt.

**What the model does *not* include today:**

- Eval judge (GPT-4o) on gold pairs — extra $ on quality runs.  
- Re-runs / prompt experiments — multiply by iteration count.  
- **Open backlog growth** — if you removed the 35-cap without a smarter retrieval, resolve input could grow super-linear.

**Interview line:**

> “At 3M documents, **~$14M in list-price LLM** is a planning number, not a quote — but the important point is **no hidden quadratic API** in v1 except *potential* open-claim blow-up if we dropped the resolver cap.”

**Show:** LLM Cost → scale table + “Pricing assumptions” expander.

---

### 12.3 Compute and memory bottlenecks

**Open-claim backlog (the real architectural pressure)**

> “After each call, unresolved claims accumulate. The resolver can’t read 500 open claims every quarter — we **pre-filter** to the most relevant **35** (same thread as new claims + keyword overlap with transcript). That’s why the design **works at 31 calls** but would **need retrieval** at real scale.”

| Scale | Open claims (illustrative) | v1 behavior | What you’d add |
|-------|---------------------------|-------------|----------------|
| 31 transcripts | ~140 open at end | Cap rarely bites | — |
| 30k docs / one mega-issuer | tens of thousands open | Cap hides most claims from resolver | **Retrieve** top-k by embedding or BM25 per transcript |
| Multi-issuer platform | per-issuer state | Shard corpus by `company_id` | Separate walk-forward state per issuer |

**Step snapshots (`data/claims/steps/`)**

> “We write **three JSON files per transcript** for audit and trace rebuild. At 3M documents that’s **9M files** unless you move to partitioned storage (S3 + manifest, or DB blobs). Git is **not** the store at that scale.”

**Single-process orchestrator**

> “`run --all` is a **single Python loop** — fine for batch overnight jobs; for 3M docs you’d queue **one job per transcript** with idempotent state in Postgres, not one giant in-memory `PipelineState`.”

**Parse throughput**

> “PDF parse is CPU-bound; scale with **worker pool** or container fleet. No model calls.”

---

### 12.4 Storage and serving

| Artifact | 31 docs | At 300k–3M docs |
|----------|---------|------------------|
| `data/parsed/` | ~31 JSON | Object store; version by hash |
| `data/claims/*.json` | ~10 MB | **PostgreSQL** or columnar (claims, resolutions, threads) |
| `data/claims/steps/` | ~31 × 3 files | Append-only event log or snapshot table; not full JSON tree in git |
| Dashboard | `load_corpus()` all in RAM | Paginated API, pre-aggregates, OLAP cubes |

**Say:**

> “JSON-in-git is a **feature for the take-home**: diffs, review, zero infra. Production is **database + API**, dashboard reads aggregates — same schema conceptually.”

---

### 12.5 Quality and eval at scale

| Concern | Pilot | At scale |
|---------|-------|----------|
| Golden labels | 54 claims, hand/Claude | **Sample** + human review queue; continuous eval on stratified sample |
| Thread merge | Subject string match | Normalization + embedding merge + human override |
| Dedup | None | Near-duplicate detection on quote hash |
| Stale rule | Fixed 120d | Per event-type grace; optional analyst override |

**Say:**

> “Scalability isn’t only dollars — it’s **trust**. You can’t manually gold-label 3M transcripts. You need **sampling**, **automated checks** (quote locate, status confusion alerts), and **human review** on exceptions.”

---

### 12.6 Tiered roadmap (what you’d say you’d build)

Use this if they ask *“What would you do at 30k? 3M?”*

**~30k documents (single enterprise, many calls)**

1. **Persist state** in DB; drop full step JSON on disk or compress to event stream.  
2. **Retriever** for open claims (top 50 by semantic + thread + date).  
3. **Incremental** `run --from <transcript_id>`.  
4. **Batch parse** on workers; sequential walk-forward per issuer.  
5. **FTS** (SQLite/Postgres) for Explorer search — still no Weaviate required.

**~300k–3M documents (platform / many issuers)**

1. **Shard** walk-forward by `issuer_id` (no cross-issuer resolution).  
2. **Tiered models** — small model triage, large model only on hard resolves.  
3. **External outcomes** optional path for numeric claims (filings API).  
4. **Materialized views** for dashboard; Streamlit → internal web app.  
5. **Cost controls** — token budgets, cache parsed+extract per transcript hash.

---

### 12.7 Is there a **limit** to the approach?

**Say (honest):**

> “The **walk-forward in-corpus** approach scales as a **batch analytics pipeline**, not as real-time streaming on every press release. The limit is less ‘can we run 3M PDFs’ and more **‘can we resolve open claims intelligently when thousands are live’** and **‘can we verify claims against reality’** without external data. For spoken-guidance tracking alone, it scales with **retrieval + storage + $**; for **ground-truth verification**, you need a second system.”

**Hard limits to name:**

1. **Sequential time** — cannot parallelize within one issuer’s timeline.  
2. **Resolver context** — 35-cap is a stand-in for retrieval; without it, quality or cost breaks.  
3. **In-corpus only** — ‘confirmed’ does not scale to ‘true’ without filings/market data.  
4. **LLM quality** — error rate × millions of claims = operational review load.

---

### 12.8 Scalability one-liners (memorize)

| Question | Answer |
|----------|--------|
| Cost at 3M docs? | ~$14M heuristic, linear in docs; show LLM Cost page |
| Biggest bottleneck? | Growing **open-claim set** → need retrieval before resolve |
| Does walk-forward parallelize? | **Not** within one timeline; yes across issuers |
| Why JSON? | Demo/repro; production → DB |
| Would you use a vector DB? | **Optional** for retrieval/search, not required for v1 truth store |
| Limit of approach? | Batch in-corpus narrative tracking yes; autonomous truth verification needs external data |

---

### 12.9 Demo hook (30 seconds)

**Show LLM Cost** → point at 31 vs 30k vs 3M bars.

**Say:**

> “This is linear extrapolation from our 31-call corpus — the slope is two LLM calls per doc with a capped resolver fan-in. The **architecture** slide is what we’d evolve: add retrieval between orchestrator and resolve, swap JSON for a store, keep walk-forward semantics.”

---

## 13. Close (2 minutes)

**Say:**

> “To recap: deterministic parse, walk-forward extract and resolve with Azure, git-backed JSON corpus with threads and step snapshots, Streamlit for exploration, pilot golden-set eval with matcher plus judge. The system is strong on **audit trail and simulation discipline**; the main gaps are **external verification**, **thread/subject consistency**, and **eval coverage** beyond the first five transcripts.”

**Offer:** “Happy to go deeper on prompts, a specific claim, or eval methodology.”

---

## Quick demo checklist (print this)

- [ ] Dashboard loads  
- [ ] Architecture tab works (click 2–3 nodes)  
- [ ] Overview numbers make sense  
- [ ] One **confirmed** claim with evidence in Claim Detail  
- [ ] One **thread** with multiple events  
- [ ] Pipeline Eval shows results (or explain `eval` if missing)  
- [ ] LLM Cost scale slide (31 / 30k / 3M)  
- [ ] Scalability talking points ready (§12)  
- [ ] `.env` / PDFs **not** shared on screen  

---

## If they only give you 15 minutes

1. Problem + walk-forward (steps 0–2) — 5 min  
2. Architecture diagram — 3 min  
3. One claim + one thread — 5 min  
4. Eval + one limitation — 2 min  

**If they ask only about scale (2 min):** Use [§12.8](#128-scalability-one-liners-memorize) + LLM Cost page.

---

## Related docs

| Doc | Use for |
|-----|---------|
| [complete-system-guide.md](complete-system-guide.md) | Deep technical reference |
| [claimwatch-architecture.html](claimwatch-architecture.html) | Visual map (also in dashboard) |
| [taxonomy.md](taxonomy.md) | Status definitions |
| [evaluation.md](evaluation.md) | Eval methodology |
| [tradeoffs.md](tradeoffs.md) | Design choices |
| [data-and-parsing.md](data-and-parsing.md) | Parse pipeline detail |

---

*Good luck with the screen-share.*
