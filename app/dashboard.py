"""ClaimWatch analytics dashboard — browse corpus claims and resolutions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

load_dotenv()


def _apply_streamlit_secrets() -> None:
    """Map Streamlit Cloud secrets → os.environ (for Azure OpenAI)."""
    try:
        for key, value in st.secrets.items():
            if isinstance(value, str) and value and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


_apply_streamlit_secrets()

from src.analytics.corpus_analytics import (
    build_resolution_timelines,
    claims_by_calendar_year,
    claims_by_quarter,
    claims_to_dataframe,
    first_resolution_event,
    hedge_status_crosstab,
    open_by_category,
    resolution_summary,
    speaker_stats,
    status_by_category,
    status_counts,
    successor_claims,
    time_to_resolution_df,
)
from src.analytics.corpus_loader import corpus_available, load_corpus
from src.analytics.status_mapping import (
    format_status_display,
    mapping_table_rows,
    pdf_outcome_label,
)
from src.analytics.pdf_citation import (
    TRANSCRIPTS_DIR,
    find_quote_page,
    get_citation,
    render_page_image,
    resolve_pdf_path,
)
from src.analytics.transcript_excerpt import get_transcript_excerpt

_EXCERPT_CSS = """
<style>
mark.cw-highlight { background-color: #fff3cd; padding: 0 2px; border-radius: 2px; }
.cw-excerpt {
    white-space: pre-wrap;
    font-size: 0.92rem;
    line-height: 1.45;
    padding: 0.75rem 1rem;
    background: #f8f9fa;
    border-left: 3px solid #3b82f6;
    border-radius: 4px;
    max-height: 280px;
    overflow-y: auto;
}
</style>
"""

STATUS_COLORS = {
    "open": "#3b82f6",
    "confirmed": "#22c55e",
    "revised": "#f59e0b",
    "failed": "#ef4444",
    "partial": "#a855f7",
    "unresolvable": "#6b7280",
    "stale": "#9ca3af",
    "n/a": "#d1d5db",
}


def _fmt_status(status: str) -> str:
    return format_status_display(status, include_internal=True)


def _claims_explorer_df(bundle) -> pd.DataFrame:
    """Explorer table with PDF outcome column (safe if module cache is stale)."""
    df = claims_to_dataframe(bundle)
    if "outcome" not in df.columns:
        df = df.copy()
        df["outcome"] = df["status"].astype(str).map(pdf_outcome_label)
    return df


@st.cache_resource
def get_bundle():
    return load_corpus()


@st.cache_data
def get_timelines(_bundle_key: str):
    bundle = get_bundle()
    return build_resolution_timelines(bundle)


@st.cache_data
def cached_excerpt(source_doc: str, quote: str) -> dict | None:
    ex = get_transcript_excerpt(source_doc, quote)
    if not ex:
        return None
    return {
        "speaker_name": ex.speaker_name,
        "speaker_role": ex.speaker_role,
        "section": ex.section,
        "chunk_index": ex.chunk_index,
        "match_quality": ex.match_quality,
        "highlighted_html": ex.highlighted_html,
    }


@st.cache_data
def cached_citation(source_doc: str, quote: str) -> dict:
    cit = get_citation(source_doc, quote)
    return {
        "pdf_path": str(cit.pdf_path) if cit.pdf_path else None,
        "pdf_filename": cit.pdf_filename,
        "page": cit.page,
        "total_pages": cit.total_pages,
        "match_method": cit.match_method,
        "speaker_turn_index": cit.speaker_turn_index,
        "found": cit.found,
    }


def _render_transcript_excerpt(source_doc: str, quote: str, key_suffix: str) -> None:
    """Parsed speaker turn with quote highlighted (verification context)."""
    ex = cached_excerpt(source_doc, quote)
    with st.expander("Transcript excerpt (highlighted)", expanded=True):
        if not ex:
            st.caption(f"No matching speaker turn in parsed `{source_doc}.json`.")
            return
        qual = ex["match_quality"]
        qual_note = "" if qual == "exact" else f" ({qual} match — quote may be shortened)"
        st.caption(
            f"{ex['speaker_name']} · {ex['section']} · turn {ex['chunk_index']}{qual_note}"
        )
        st.markdown(_EXCERPT_CSS, unsafe_allow_html=True)
        st.markdown(
            f'<div class="cw-excerpt">{ex["highlighted_html"]}</div>',
            unsafe_allow_html=True,
        )


def _render_one_pdf_citation(
    claim_id: str,
    *,
    label: str,
    source_doc: str,
    quote: str,
    key_suffix: str,
) -> None:
    """PDF page preview (A) + parsed transcript excerpt (B)."""
    st.markdown(f"**{label}** (`{source_doc}`)")

    _render_transcript_excerpt(source_doc, quote, key_suffix)

    cit = cached_citation(source_doc, quote)
    if not cit["found"]:
        st.warning(
            f"PDF not found for `{source_doc}`. "
            f"Place transcripts in `{TRANSCRIPTS_DIR}`."
        )
        return

    page_hint = cit["page"]
    fn = cit["pdf_filename"] or Path(cit["pdf_path"]).name
    method = cit["match_method"]
    st.markdown(
        f"PDF: `{fn}` · **page {page_hint or '?'}** ({method}) · "
        f"turn {cit['speaker_turn_index'] if cit['speaker_turn_index'] is not None else '—'}"
    )
    if not page_hint:
        st.caption("Page not auto-located — use Re-locate or scan adjacent pages.")

    show_pdf = st.checkbox(
        "Open PDF page preview",
        key=f"view_pdf_{claim_id}_{key_suffix}",
    )
    if not show_pdf:
        return

    pdf_path = Path(cit["pdf_path"])
    total = cit["total_pages"] or 200
    default_page = int(page_hint or 1)

    c1, c2 = st.columns([1, 3])
    with c1:
        page_num = st.number_input(
            "Page",
            min_value=1,
            max_value=max(total, 1),
            value=min(default_page, max(total, 1)),
            key=f"page_num_{claim_id}_{key_suffix}",
        )
        if st.button("Re-locate quote", key=f"reloc_{claim_id}_{key_suffix}"):
            p, m = find_quote_page(str(pdf_path.resolve()), quote)
            if p:
                st.success(f"Found on page {p} ({m})")
            else:
                st.info("Quote not matched in PDF text. Try adjacent pages.")

        with open(pdf_path, "rb") as f:
            st.download_button(
                "Download PDF",
                f.read(),
                file_name=fn,
                mime="application/pdf",
                key=f"dl_{claim_id}_{key_suffix}",
            )

    with c2:
        try:
            img = render_page_image(pdf_path, int(page_num))
            if img is not None:
                st.image(img, caption=f"{fn} — page {page_num}", use_container_width=True)
            else:
                st.warning("Could not render page image.")
        except Exception as e:
            st.warning(f"Page preview unavailable: {e}")

    try:
        st.pdf(str(pdf_path), height=520)
    except (AttributeError, TypeError, Exception):
        st.caption("Use page preview or download if inline PDF is unavailable.")


def render_pdf_citation(
    claim_id: str,
    source_doc: str,
    quote: str,
    *,
    evidence_quote: str | None = None,
    resolved_at_transcript: str | None = None,
    resolution_status: str = "open",
):
    """
    Analyst citations: original utterance PDF + (if resolved) evidence PDF on the resolving call.
    """
    st.markdown("#### PDF citations (verify as analyst)")

    _render_one_pdf_citation(
        claim_id,
        label="1. Where the claim was said",
        source_doc=source_doc,
        quote=quote,
        key_suffix="uttered",
    )

    terminal = resolution_status not in ("open", "n/a")
    if terminal and evidence_quote and resolved_at_transcript:
        if resolved_at_transcript != source_doc:
            st.divider()
            _render_one_pdf_citation(
                claim_id,
                label=f"2. Where it was {_fmt_status(resolution_status)} (resolution evidence)",
                source_doc=resolved_at_transcript,
                quote=evidence_quote,
                key_suffix="resolved",
            )
        else:
            st.caption(
                "Resolution evidence is in the same transcript as the claim — "
                "use section 1 and search for the evidence quote on nearby pages."
            )
    elif terminal and not evidence_quote:
        st.info("No evidence quote stored for this resolution — cannot locate a PDF page.")
    elif terminal and not resolved_at_transcript:
        st.caption(
            "Run `rebuild-trace` to attach resolving transcript id, or check resolution timeline."
        )


def page_overview(bundle, timelines):
    summary = resolution_summary(bundle)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total claims", summary["total"])
    c2.metric("Open", summary["open"])
    c3.metric("Resolved / terminal", summary["terminal"])
    c4.metric("% not open", f"{summary['pct_resolved']}%")
    c5.metric("Threads", bundle.claims_threads.total_threads)

    st.caption(bundle.corpus_label)

    col_a, col_b = st.columns(2)
    with col_a:
        sc = status_counts(bundle)
        fig = px.pie(
            values=sc.values,
            names=sc.index,
            labels=[_fmt_status(str(s)) for s in sc.index],
            title="Claims by outcome (take-home mapping)",
            color=sc.index,
            color_discrete_map=STATUS_COLORS,
        )
        fig.update_layout(height=380, margin=dict(t=40, b=20, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True)

    with col_b:
        yearly = claims_by_calendar_year(bundle)
        if not yearly.empty:
            fig2 = px.bar(
                yearly,
                x="calendar_year",
                y="claims",
                title="Claims by calendar year (date_made)",
            )
            fig2.update_layout(height=380, margin=dict(t=40, b=20, l=20, r=20))
            st.plotly_chart(fig2, use_container_width=True)

    qdf = claims_by_quarter(bundle)
    if not qdf.empty:
        fig3 = px.bar(
            qdf,
            x="label",
            y="claims",
            title="Earnings-call claims by fiscal year + quarter",
        )
        fig3.update_layout(height=360, margin=dict(t=40, b=80, l=20, r=20))
        st.plotly_chart(fig3, use_container_width=True)


def _dataframe_row_selection(filtered: pd.DataFrame, display_cols: list[str]) -> str | None:
    """Return selected claim_id from interactive table, if supported."""
    try:
        event = st.dataframe(
            filtered[display_cols],
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="explorer_table",
        )
        if event is not None and hasattr(event, "selection") and event.selection.rows:
            row_idx = event.selection.rows[0]
            return str(filtered.iloc[row_idx]["claim_id"])
    except TypeError:
        st.dataframe(
            filtered[display_cols],
            use_container_width=True,
            hide_index=True,
        )
    return None


def page_explorer(bundle, timelines):
    df = _claims_explorer_df(bundle)

    st.subheader("Claims Explorer")
    st.caption("Click a row in the table to open claim details below.")

    f1, f2, f3, f4, f5 = st.columns(5)
    years = sorted(df["calendar_year"].unique())
    year_sel = f1.multiselect("Year", years, default=years)
    quarters = sorted([q for q in df["quarter"].unique() if q])
    quarter_sel = f2.multiselect("Quarter", quarters, default=quarters)
    statuses = sorted(df["status"].unique())
    status_sel = f3.multiselect(
        "Status",
        statuses,
        default=statuses,
        format_func=_fmt_status,
    )
    categories = sorted(df["category"].unique())
    cat_sel = f4.multiselect("Category", categories, default=categories)
    speakers = sorted(df["speaker"].unique())
    sp_sel = f5.multiselect("Speaker", speakers, default=speakers)

    section_sel = st.multiselect(
        "Section",
        sorted(df["source_section"].unique()),
        default=list(df["source_section"].unique()),
    )

    mask = (
        df["calendar_year"].isin(year_sel)
        & df["status"].isin(status_sel)
        & df["category"].isin(cat_sel)
        & df["speaker"].isin(sp_sel)
        & df["source_section"].isin(section_sel)
    )
    if quarter_sel:
        mask &= (df["quarter"].isin(quarter_sel)) | (df["quarter"] == "")
    filtered = df[mask].copy().reset_index(drop=True)

    st.write(f"**{len(filtered)}** claims matching filters")
    display_cols = [
        "claim_id",
        "date_made",
        "quarter",
        "speaker",
        "category",
        "subject",
        "outcome",
        "status",
        "hedge_level",
        "source_section",
    ]

    table_cols = [c for c in display_cols if c in filtered.columns]
    selected_from_table = _dataframe_row_selection(filtered, table_cols)
    if selected_from_table:
        st.session_state.selected_claim_id = selected_from_table

    st.download_button(
        "Download CSV",
        filtered.to_csv(index=False).encode("utf-8"),
        "claims_filtered.csv",
        "text/csv",
    )

    st.divider()
    claim_ids = filtered["claim_id"].tolist()
    default_id = st.session_state.get("selected_claim_id")
    if default_id not in claim_ids and claim_ids:
        default_id = claim_ids[0]

    if claim_ids:
        pick = st.selectbox(
            "Or pick claim ID",
            claim_ids,
            index=claim_ids.index(default_id) if default_id in claim_ids else 0,
            key="explorer_pick",
        )
        if pick:
            st.session_state.selected_claim_id = pick

    selected = st.session_state.get("selected_claim_id")
    if selected and selected in bundle.claim_by_id:
        st.markdown(f"### Selected: `{selected}`")
        with st.container(border=True):
            render_claim_detail(bundle, timelines, selected)


def render_claim_detail(bundle, timelines, claim_id: str):
    ec = bundle.claim_by_id.get(claim_id)
    if not ec:
        st.warning("Claim not found.")
        return

    c = ec.claim
    st.markdown(f"#### {c.claim_id} — {c.subject}")
    st.markdown(
        f"**Outcome:** {_fmt_status(c.resolution.status)} · **Speaker:** {c.speaker} · "
        f"**Date:** {c.date_made} · **Section:** {getattr(c.source_section, 'value', c.source_section)}"
    )
    st.caption(f"Transcript: `{c.source_doc}`")
    res = c.resolution
    if res.resolved_at_date:
        st.markdown(
            f"**First said:** {c.date_made} (`{c.source_doc}`) · "
            f"**Resolved:** {res.resolved_at_date} (`{res.resolved_at_transcript or '—'}`)"
        )
    else:
        st.markdown(f"**First said:** {c.date_made} (`{c.source_doc}`) · **Still open**")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Paraphrase**")
        st.info(c.paraphrase)
        st.markdown("**Verbatim quote**")
        st.markdown(f"> {c.quote}")
    with col2:
        st.markdown("**Metadata**")
        st.json(
            {
                "category": c.category,
                "timeframe": c.timeframe,
                "target_value": c.target_value,
                "hedge_level": c.hedge_level,
                "falsifiable": c.falsifiable,
                "thread_id": c.thread_id,
            }
        )
        if c.resolution.evidence_quote:
            st.markdown("**Resolution evidence**")
            st.success(c.resolution.evidence_quote)
        if c.resolution.resolution_notes:
            st.caption(c.resolution.resolution_notes)

    render_pdf_citation(
        claim_id,
        c.source_doc,
        c.quote,
        evidence_quote=c.resolution.evidence_quote,
        resolved_at_transcript=c.resolution.resolved_at_transcript,
        resolution_status=c.resolution.status,
    )

    st.markdown("#### Resolution timeline")
    events = timelines.get(claim_id, [])
    if not events:
        st.caption("No step snapshots — showing final status only.")
        st.write(f"Final: **{_fmt_status(c.resolution.status)}**")
    else:
        for ev in events:
            extra = ""
            if ev.evidence_quote:
                q = ev.evidence_quote
                extra = f" — _{q[:120]}…_" if len(q) > 120 else f" — _{q}_"
            st.markdown(
                f"- **{ev.transcript_date}** `{ev.transcript_id}` → **{_fmt_status(ev.status)}**{extra}"
            )
        first = first_resolution_event(timelines, claim_id)
        if first and first.status != "open":
            days = (first.transcript_date - c.date_made).days
            st.caption(f"First left `open` on {first.transcript_date} ({days} days after claim).")

    succ = successor_claims(bundle, ec)
    if succ:
        st.markdown("#### Revision successors (`resolved_by`)")
        for s in succ:
            st.markdown(f"- **{s.claim_id}** ({s.date_made}): {s.claim.paraphrase[:200]}")

    if c.thread_id and c.thread_id in bundle.thread_by_id:
        render_thread_trace(bundle.thread_by_id[c.thread_id])


def render_thread_trace(thread):
    st.markdown(f"#### Thread: {thread.subject} (`{thread.thread_id}`)")
    if thread.first_said_date or thread.first_date:
        st.caption(
            f"First said: {thread.first_said_date or thread.first_date} · "
            f"Resolved: {thread.resolved_at_date or '—'} "
            f"({thread.resolved_at_transcript or '—'}) · Final: **{_fmt_status(thread.final_status)}**"
        )
    trace = getattr(thread, "trace", None) or []
    if trace:
        st.markdown("**Thread trace (chronological)**")
        for ev in trace:
            if ev.event == "uttered":
                st.markdown(
                    f"- 📢 **{ev.date}** `{ev.transcript_id}` · **{ev.claim_id}** · "
                    f"{ev.speaker} · *{ev.target_value}* → {_fmt_status(ev.status)}"
                )
            else:
                by = f" → superseded by {', '.join(ev.resolved_by_claim_ids)}" if ev.resolved_by_claim_ids else ""
                st.markdown(
                    f"- ⚖️ **{ev.date}** `{ev.transcript_id}` · **{ev.claim_id}** → "
                    f"**{_fmt_status(ev.status)}**{by}"
                )
                if ev.evidence_quote:
                    st.caption(ev.evidence_quote[:200])
    else:
        for cid in thread.claim_ids:
            st.markdown(f"- `{cid}`")


def page_claim_detail(bundle, timelines):
    st.subheader("Claim detail")
    ids = sorted(bundle.claim_by_id.keys())
    default = st.session_state.get("selected_claim_id")
    idx = ids.index(default) if default in ids else 0
    pick = st.selectbox("Claim ID", ids, index=idx, key="detail_pick")
    if pick:
        st.session_state.selected_claim_id = pick
        render_claim_detail(bundle, timelines, pick)


def page_threads(bundle, timelines):
    st.subheader("Threads")
    threads = sorted(bundle.thread_by_id.values(), key=lambda t: t.subject.lower())
    labels = [
        f"{t.subject} ({t.n_claims} claims, {_fmt_status(t.final_status)})" for t in threads
    ]
    idx = st.selectbox("Select thread", range(len(threads)), format_func=lambda i: labels[i])
    thread = threads[idx]

    render_thread_trace(thread)

    if thread.evolution:
        ev_df = pd.DataFrame([e.model_dump() for e in thread.evolution])
        fig = px.scatter(
            ev_df,
            x="date",
            y="target_value",
            text="claim_id",
            color="status_after_this_utterance",
            title="Thread evolution (target over time)",
            color_discrete_map=STATUS_COLORS,
        )
        fig.update_traces(textposition="top center")
        fig.update_layout(height=400, margin=dict(t=40, b=20, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True)

    for cid in thread.claim_ids:
        if cid in bundle.claim_by_id:
            with st.expander(f"{cid} — {bundle.claim_by_id[cid].claim.paraphrase[:80]}…"):
                render_claim_detail(bundle, timelines, cid)


def page_resolution(bundle, timelines):
    st.subheader("Resolution analytics")
    st.caption(
        "Internal statuses map to take-home outcomes — see sidebar **Outcome mapping** or docs/taxonomy.md."
    )

    col1, col2 = st.columns(2)
    with col1:
        cat_ct = status_by_category(bundle)
        st.markdown("**Status by category**")
        st.dataframe(cat_ct, use_container_width=True)

    with col2:
        hedge_ct = hedge_status_crosstab(bundle)
        st.markdown("**Hedge level × status**")
        st.dataframe(hedge_ct, use_container_width=True)
        fig = px.imshow(
            hedge_ct,
            text_auto=True,
            aspect="auto",
            title="Hedge × status heatmap",
            color_continuous_scale="Blues",
        )
        fig.update_layout(height=320, margin=dict(t=40, b=20, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True)

    ttr = time_to_resolution_df(bundle, timelines)
    if not ttr.empty:
        st.markdown("**Time to first resolution**")
        fig2 = px.histogram(
            ttr,
            x="days_to_resolve",
            color="final_status",
            nbins=30,
            title="Days until status left open",
            color_discrete_map=STATUS_COLORS,
        )
        fig2.update_layout(height=360, margin=dict(t=40, b=20, l=20, r=20))
        st.plotly_chart(fig2, use_container_width=True)
        st.dataframe(ttr.sort_values("days_to_resolve"), use_container_width=True, hide_index=True)
    else:
        st.info("No step snapshots or no resolved claims for time-to-resolve chart.")

    open_cat = open_by_category(bundle)
    if not open_cat.empty:
        fig3 = px.bar(
            x=open_cat.index,
            y=open_cat.values,
            labels={"x": "category", "y": "open claims"},
            title="Open claims by category",
        )
        fig3.update_layout(height=320, margin=dict(t=40, b=80, l=20, r=20))
        st.plotly_chart(fig3, use_container_width=True)


EVAL_RESULTS_PATH = _ROOT / "data" / "eval" / "results.json"


@st.cache_data
def load_eval_results() -> dict | None:
    if not EVAL_RESULTS_PATH.is_file():
        return None
    return json.loads(EVAL_RESULTS_PATH.read_text(encoding="utf-8"))


def _eval_metric_card(label: str, good: int, total: int, pct: float | None) -> None:
    if total:
        st.metric(label, f"{good}/{total}", f"{pct}%" if pct is not None else None)
    else:
        st.metric(label, "n/a")


@st.cache_data
def load_cost_report(_corpus_dir: str):
    from src.analytics.cost_estimate import build_corpus_cost_report
    from src.analytics.corpus_loader import load_corpus

    return build_corpus_cost_report(load_corpus(Path(_corpus_dir)))


def page_llm_cost(_bundle, _timelines):
    st.subheader("LLM cost (Azure GPT-4o-mini)")
    st.caption(
        "Walk-forward pipeline: one **extract** + one **resolve** call per transcript. "
        "Figures are estimates from transcript size and step snapshots unless you have logged usage from a fresh `run --all`."
    )

    report = load_cost_report(str(_bundle.corpus_dir))
    if not report.steps:
        st.warning("No step snapshots under `data/claims/steps/` — run the pipeline first.")
        return

    st.markdown("#### This corpus")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Transcripts", report.n_transcripts)
    m2.metric("Claims", report.n_claims)
    m3.metric("Est. pipeline total", f"${report.total_usd:,.2f}")
    m4.metric("Est. per transcript", f"${report.per_transcript_usd:,.3f}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Extract (pass 1)", f"${report.extract_usd:,.2f}")
    c2.metric("Resolve (pass 2)", f"${report.resolve_usd:,.2f}")
    c3.metric(
        "Tokens in / out",
        f"{report.extract_tokens_in + report.resolve_tokens_in:,} / "
        f"{report.extract_tokens_out + report.resolve_tokens_out:,}",
    )

    with st.expander("Pricing assumptions", expanded=False):
        st.markdown(
            f"""
| Setting | Value |
|---------|-------|
| Model | `{report.model}` |
| Input | **${report.input_usd_per_1m:.2f}** / 1M tokens |
| Output | **${report.output_usd_per_1m:.2f}** / 1M tokens |

{report.source_note}

Override list prices with env `AZURE_MINI_INPUT_USD_PER_1M` and `AZURE_MINI_OUTPUT_USD_PER_1M`.
            """
        )
        if report.observed_extract_usd is not None:
            st.info(
                f"`data/stats/extraction_summary.json` logged **${report.observed_extract_usd:,.2f}** "
                f"for extract-only on a subset of transcripts (resolve not included)."
            )

    st.markdown("#### By transcript (estimate)")
    step_rows = [
        {
            "transcript": s.transcript_id,
            "new_claims": s.new_claims,
            "open_before": s.open_before,
            "fed_resolve": s.fed_to_resolver,
            "extract_$": s.extract_usd,
            "resolve_$": s.resolve_usd,
            "total_$": round(s.total_usd, 4),
        }
        for s in report.steps
    ]
    st.dataframe(pd.DataFrame(step_rows), use_container_width=True, hide_index=True)

    st.markdown("#### Scale projections")
    st.caption(
        "Linear extrapolation using average **$/transcript** from this corpus "
        "(same mix of earnings calls / AGMs / ESG; very large corpora may differ)."
    )

    from src.analytics.cost_estimate import scale_projection

    scale_rows = scale_projection(
        report,
        [report.n_transcripts, 30_000, 300_000, 3_000_000],
    )
    scale_df = pd.DataFrame(scale_rows)
    display_df = scale_df.copy()
    display_df["documents"] = display_df["documents"].map(
        lambda n: f"{n:,}" if n >= 1000 else str(n)
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    fig = px.bar(
        scale_df,
        x="documents",
        y="total_usd",
        title="Estimated total pipeline cost by corpus size",
        labels={"total_usd": "USD", "documents": "Documents"},
    )
    fig.update_layout(height=360, margin=dict(t=40, b=80, l=20, r=20))
    st.plotly_chart(fig, use_container_width=True)


def page_pipeline_eval(_bundle, _timelines):
    st.subheader("Pipeline evaluation")
    st.caption(
        "Gold: `data/eval/gold/` (demo-gold) vs pipeline: `data/claims/`. "
        "Run `uv run python -m src.main eval` to refresh."
    )

    results = load_eval_results()
    if not results:
        st.warning(
            "No `data/eval/results.json` yet. From the project root run:\n\n"
            "`uv run python -m src.main eval`"
        )
        return

    st.caption(f"Last run: {results.get('generated_at', 'unknown')}")

    ext = results["extraction_agent"]
    res = results["resolution_agent"]
    repro = ext["reproduction"]

    st.markdown("#### Extraction agent")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Gold claims", ext["gold_total"])
    c2.metric("Pipeline claims", ext["predicted_total"])
    c3.metric("Precision", f"{repro['precision_pct']}%")
    c4.metric("Recall", f"{repro['recall_pct']}%")
    st.caption(repro.get("summary", ""))

    llm_ext = ext["llm_judge_on_matched_pairs"]
    cols = st.columns(4)
    for col, key in zip(
        cols,
        ("reproduces_gold", "contextually_relevant", "quote_supported", "all_criteria_pass"),
    ):
        m = llm_ext[key]
        with col:
            _eval_metric_card(m["label"], m["good"], m["total"], m.get("accuracy_pct"))

    by_t = ext.get("by_transcript") or []
    if by_t:
        rows = []
        for t in by_t:
            r = t["reproduction"]
            lj = t.get("llm_judge", {}).get("all_criteria_pass", {})
            rows.append(
                {
                    "transcript": t["transcript_id"],
                    "gold": t["gold_total"],
                    "pipeline": t["predicted_total"],
                    "matched": r["true_positives"],
                    "precision_%": r["precision_pct"],
                    "recall_%": r["recall_pct"],
                    "f1_%": r["f1_pct"],
                    "llm_all_pass_%": lj.get("accuracy_pct"),
                }
            )
        st.markdown("##### By transcript")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("#### Resolution agent")
    r1, r2, r3 = st.columns(3)
    r1.metric("Gold resolutions", res["gold_total"])
    r2.metric("Matched", res["reproduction"]["true_positives"])
    r3.metric("Recall (pairing)", f"{res['reproduction']['recall_pct']}%")

    sem = res.get("status_exact_match", {})
    st.caption(
        f"Status exact match: {sem.get('good', 0)}/{sem.get('total', 0)} "
        f"({sem.get('accuracy_pct')}%)"
    )

    llm_res = res["llm_judge_on_matched_pairs"]
    cols2 = st.columns(4)
    for col, key in zip(
        cols2,
        (
            "reproduces_gold_status",
            "evidence_relevant",
            "resolution_contextually_sound",
            "all_criteria_pass",
        ),
    ):
        m = llm_res[key]
        with col:
            _eval_metric_card(m["label"], m["good"], m["total"], m.get("accuracy_pct"))

    chart_rows = []
    for agent, block in (("Extraction", llm_ext), ("Resolution", llm_res)):
        for key, m in block.items():
            if key == "judged_pairs" or not m.get("total"):
                continue
            chart_rows.append(
                {
                    "agent": agent,
                    "metric": m["label"],
                    "accuracy_pct": m.get("accuracy_pct") or 0,
                }
            )
    if chart_rows:
        fig = px.bar(
            pd.DataFrame(chart_rows),
            x="metric",
            y="accuracy_pct",
            color="agent",
            barmode="group",
            title="LLM judge accuracy (%)",
            labels={"accuracy_pct": "Accuracy %", "metric": ""},
        )
        fig.update_layout(height=360, margin=dict(t=40, b=120), xaxis_tickangle=-25)
        st.plotly_chart(fig, use_container_width=True)


def page_speakers(bundle, _timelines):
    st.subheader("Speakers")
    sp = speaker_stats(bundle)
    if sp.empty:
        st.info("No speaker data.")
        return

    fig = px.bar(
        sp,
        x="speaker",
        y="claims",
        title="Claims per speaker",
        hover_data=["confirmed", "failed", "open", "confirm_rate_pct"],
    )
    fig.update_layout(height=380, margin=dict(t=40, b=80, l=20, r=20))
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(sp, use_container_width=True, hide_index=True)


def main():
    st.set_page_config(
        page_title="ClaimWatch Analytics",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    if not corpus_available():
        st.error(
            "Corpus not found. Run the pipeline first:\n\n"
            "`uv run python -m src.main run --all`"
        )
        st.stop()

    try:
        bundle = get_bundle()
    except FileNotFoundError as e:
        st.error(str(e))
        st.stop()

    timelines = get_timelines(str(bundle.corpus_dir))

    st.sidebar.title("ClaimWatch")
    st.sidebar.caption(bundle.corpus_label)
    st.sidebar.caption(f"Data: `{bundle.corpus_dir}`")
    pdf_ok = resolve_pdf_path(bundle.enriched[0].claim.source_doc) if bundle.enriched else None
    if pdf_ok:
        st.sidebar.success(f"PDFs: `{TRANSCRIPTS_DIR}`")
    else:
        st.sidebar.warning(f"PDFs not found in `{TRANSCRIPTS_DIR}`")

    if not bundle.step_snapshots:
        st.sidebar.warning("No step snapshots — resolution timelines limited.")

    with st.sidebar.expander("Outcome mapping (brief ↔ corpus)", expanded=False):
        st.caption("Take-home: materialized / partial / didn't / unresolvable")
        st.dataframe(
            pd.DataFrame(mapping_table_rows()),
            use_container_width=True,
            hide_index=True,
        )
        st.caption("[docs/taxonomy.md](docs/taxonomy.md)")

    nav_options = [
        "Overview",
        "LLM Cost",
        "Pipeline Eval",
        "Claims Explorer",
        "Claim Detail",
        "Threads",
        "Resolution Analytics",
        "Speakers",
    ]
    default_nav = st.session_state.pop("nav_page", nav_options[0])
    if default_nav not in nav_options:
        default_nav = nav_options[0]
    page = st.sidebar.radio("Navigate", nav_options, index=nav_options.index(default_nav))

    pages = {
        "Overview": lambda: page_overview(bundle, timelines),
        "LLM Cost": lambda: page_llm_cost(bundle, timelines),
        "Pipeline Eval": lambda: page_pipeline_eval(bundle, timelines),
        "Claims Explorer": lambda: page_explorer(bundle, timelines),
        "Claim Detail": lambda: page_claim_detail(bundle, timelines),
        "Threads": lambda: page_threads(bundle, timelines),
        "Resolution Analytics": lambda: page_resolution(bundle, timelines),
        "Speakers": lambda: page_speakers(bundle, timelines),
    }
    pages[page]()


if __name__ == "__main__":
    main()
