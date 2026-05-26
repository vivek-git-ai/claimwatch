"""
ClaimWatch CLI

Usage:
  python -m src.main inspect --transcript <filename_or_index>
  python -m src.main list
  python -m src.main run [--limit N]
  python -m src.main restore
  python -m src.main check
  python -m src.main status
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text

load_dotenv()

console = Console()

TRANSCRIPTS_DIR = Path(os.getenv("TRANSCRIPTS_DIR", "docs/transcripts"))
PARSED_DIR = Path(os.getenv("PARSED_DIR", "data/parsed"))


def _get_transcript_paths() -> list[Path]:
    """Return all PDF transcript paths sorted chronologically by filename date."""
    from src.ingestion.transcript_metadata import extract_metadata

    paths = sorted(TRANSCRIPTS_DIR.glob("*.pdf"))
    dated = []
    for p in paths:
        try:
            meta = extract_metadata(p)
            dated.append((meta.transcript_date, p))
        except Exception:
            dated.append((None, p))

    dated.sort(key=lambda x: (x[0] is None, x[0]))
    return [p for _, p in dated]


def cmd_list(args: list[str]) -> None:
    """List all transcripts with parsed metadata."""
    from src.ingestion.transcript_metadata import extract_metadata

    paths = _get_transcript_paths()
    table = Table(title="Transcripts", box=box.ROUNDED, show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Date", style="cyan", width=12)
    table.add_column("Event Type", style="green", width=22)
    table.add_column("Quarter", width=8)
    table.add_column("Year", width=6)
    table.add_column("Filename", style="dim")

    for i, path in enumerate(paths):
        try:
            meta = extract_metadata(path)
            table.add_row(
                str(i),
                str(meta.transcript_date),
                meta.event_type.value,
                meta.quarter or "-",
                str(meta.year),
                path.name[:70],
            )
        except Exception as e:
            table.add_row(str(i), "?", "ERROR", "-", "-", f"{path.name[:50]} ({e})")

    console.print(table)
    console.print(f"\n[dim]Total: {len(paths)} transcripts in {TRANSCRIPTS_DIR}[/dim]")


def cmd_inspect(args: list[str]) -> None:
    """Inspect parsed output of a single transcript."""
    from src.ingestion.pdf_parser import parse_transcript
    from src.models.schema import Section

    if not args:
        console.print("[red]Usage: inspect <filename_or_index>[/red]")
        sys.exit(1)

    target = args[0]
    paths = _get_transcript_paths()

    # Resolve by index or filename substring
    path = None
    if target.isdigit():
        idx = int(target)
        if 0 <= idx < len(paths):
            path = paths[idx]
    else:
        matches = [p for p in paths if target.lower() in p.name.lower()]
        if matches:
            path = matches[0]

    if path is None:
        console.print(f"[red]Transcript not found: {target}[/red]")
        sys.exit(1)

    console.print(f"\n[bold cyan]Parsing:[/bold cyan] {path.name}\n")

    with console.status("Parsing PDF..."):
        result = parse_transcript(path)

    meta = result.metadata

    # Metadata panel
    meta_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    meta_table.add_column("Key", style="dim")
    meta_table.add_column("Value", style="bold")
    meta_table.add_row("Date", str(meta.transcript_date))
    meta_table.add_row("Event Type", meta.event_type.value)
    meta_table.add_row("Quarter", meta.quarter or "-")
    meta_table.add_row("Year", str(meta.year))
    meta_table.add_row("Total Pages", str(result.total_pages))
    meta_table.add_row("Speaker Turns", str(len(result.speaker_turns)))
    mgmt_turns = [t for t in result.speaker_turns if t.section == Section.MGMT_DISCUSSION]
    qa_turns = [t for t in result.speaker_turns if t.section == Section.QA]
    meta_table.add_row("Mgmt Discussion Turns", str(len(mgmt_turns)))
    meta_table.add_row("Q&A Turns", str(len(qa_turns)))
    console.print(Panel(meta_table, title="[bold]Metadata[/bold]", border_style="cyan"))

    # Speaker turns table
    turns_table = Table(title="Speaker Turns", box=box.ROUNDED, show_lines=True)
    turns_table.add_column("#", style="dim", width=4)
    turns_table.add_column("Section", width=18)
    turns_table.add_column("Speaker", style="bold", width=25)
    turns_table.add_column("Role", width=35)
    turns_table.add_column("Mgmt?", width=6)
    turns_table.add_column("Text preview", width=60)

    for turn in result.speaker_turns:
        preview = turn.text.replace("\n", " ")[:120] + ("..." if len(turn.text) > 120 else "")
        mgmt_badge = "[green]✓[/green]" if turn.is_management else "[dim]✗[/dim]"
        section_color = "yellow" if turn.section == Section.MGMT_DISCUSSION else "blue"
        turns_table.add_row(
            str(turn.chunk_index),
            f"[{section_color}]{turn.section.value}[/{section_color}]",
            turn.speaker_name,
            turn.speaker_role[:35],
            mgmt_badge,
            preview,
        )

    console.print(turns_table)

    # Show a sample management turn in full
    mgmt_only = [t for t in result.speaker_turns if t.is_management and t.section == Section.MGMT_DISCUSSION]
    if mgmt_only:
        sample = mgmt_only[0]
        console.print(
            Panel(
                sample.text[:1500] + ("..." if len(sample.text) > 1500 else ""),
                title=f"[bold]Sample mgmt turn — {sample.speaker_name}[/bold]",
                border_style="green",
            )
        )


def _resolve_transcript_selection(args: list[str]) -> list[tuple[int, Path]]:
    """Resolve CLI args to (index, path) pairs. Empty args = error."""
    all_paths = _get_transcript_paths()
    if not args:
        return []

    if args[0] in ("--all", "all"):
        return list(enumerate(all_paths))

    target = " ".join(args)
    if target.isdigit():
        idx = int(target)
        if 0 <= idx < len(all_paths):
            return [(idx, all_paths[idx])]
        console.print(f"[red]Index out of range: {idx} (0-{len(all_paths) - 1})[/red]")
        sys.exit(1)

    matches = [p for p in all_paths if target.lower() in p.name.lower()]
    if len(matches) == 1:
        idx = all_paths.index(matches[0])
        return [(idx, matches[0])]
    if len(matches) > 1:
        console.print(f"[red]Multiple matches for '{target}'. Use index from 'list'.[/red]")
        for p in matches[:5]:
            console.print(f"  [dim]{all_paths.index(p)}: {p.name[:70]}[/dim]")
        sys.exit(1)

    console.print(f"[red]No transcript matching: {target}[/red]")
    sys.exit(1)


def _parse_one(index: int, path: Path, output_dir: Path) -> dict:
    """Parse a single PDF and write JSON. Returns summary row dict."""
    import json
    from src.ingestion.pdf_parser import parse_transcript
    from src.ingestion.transcript_metadata import extract_metadata

    meta = extract_metadata(path)
    result = parse_transcript(path)

    q_suffix = f"_{meta.quarter}" if meta.quarter else ""
    out_name = f"{index:02d}_{meta.transcript_date}_{meta.event_type.value}{q_suffix}.json"
    out_path = output_dir / out_name

    data = result.model_dump(mode="json")
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    mgmt_turns = sum(1 for t in result.speaker_turns if t.is_management)
    return {
        "index": index,
        "filename": out_name,
        "transcript_date": str(meta.transcript_date),
        "event_type": meta.event_type.value,
        "quarter": meta.quarter,
        "year": meta.year,
        "total_turns": len(result.speaker_turns),
        "mgmt_turns": mgmt_turns,
        "total_pages": result.total_pages,
        "status": "ok",
        "output_path": str(out_path),
    }


def cmd_parse(args: list[str]) -> None:
    """Parse transcript(s) and save to data/parsed/ as JSON files.

    Usage:
      parse 0              # single transcript by index (see 'list')
      parse Q1 2021        # single transcript by filename substring
      parse --all          # all 31 transcripts
    """
    import json
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

    if not args:
        console.print(
            "[yellow]Usage:[/yellow]\n"
            "  [cyan]python -m src.main parse 0[/cyan]         one transcript (index from list)\n"
            "  [cyan]python -m src.main parse Q1 2021[/cyan]  one transcript (filename match)\n"
            "  [cyan]python -m src.main parse --all[/cyan]    all transcripts\n"
        )
        return

    output_dir = Path("data/parsed")
    output_dir.mkdir(parents=True, exist_ok=True)

    selection = _resolve_transcript_selection(args)
    console.print(
        f"\n[bold]Parsing {len(selection)} transcript(s) to [cyan]data/parsed/[/cyan][/bold]\n"
    )

    summary = []
    errors = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Parsing...", total=len(selection))

        for index, path in selection:
            try:
                row = _parse_one(index, path, output_dir)
                summary.append(row)
                progress.update(task, advance=1, description=f"[green]{row['filename']}[/green]")
            except Exception as e:
                errors.append({"index": index, "path": str(path), "error": str(e)})
                progress.update(task, advance=1, description=f"[red]ERROR: {path.name[:40]}[/red]")

    if args[0] in ("--all", "all"):
        summary_path = Path("data/parse_summary.json")
        summary_path.write_text(
            json.dumps({"transcripts": summary, "errors": errors}, indent=2),
            encoding="utf-8",
        )

    # Print results table
    table = Table(title="Parse Results", box=box.ROUNDED)
    table.add_column("#", width=4, style="dim")
    table.add_column("Date", width=12)
    table.add_column("Event", width=22)
    table.add_column("Pages", width=6)
    table.add_column("Turns", width=6)
    table.add_column("Mgmt", width=6)
    table.add_column("File", style="dim")

    for s in summary:
        table.add_row(
            str(s["index"]),
            s["transcript_date"],
            s["event_type"],
            str(s["total_pages"]),
            str(s["total_turns"]),
            str(s["mgmt_turns"]),
            s["filename"],
        )

    for e in errors:
        table.add_row(str(e["index"]), "ERROR", e["error"][:30], "-", "-", "-", e["path"][:40], style="red")

    console.print(table)
    console.print(f"\n[bold green]{len(summary)} parsed OK[/bold green]  [red]{len(errors)} errors[/red]")
    if summary:
        console.print(f"[dim]Saved: [cyan]{summary[0]['output_path']}[/cyan][/dim]")
    from src.stats.reports import write_parse_stats

    stats_path = write_parse_stats()
    console.print(f"[dim]Stats: [cyan]{stats_path}[/cyan] (and data/stats/parse_summary.json)[/dim]")
    console.print()


def _get_parsed_json_paths() -> list[Path]:
    return sorted(PARSED_DIR.glob("*.json"))


def _resolve_parsed_json_selection(args: list[str]) -> list[Path]:
    paths = _get_parsed_json_paths()
    if not paths:
        console.print(f"[red]No parsed JSON in {PARSED_DIR}. Run 'parse' first.[/red]")
        sys.exit(1)
    if not args:
        return []
    if args[0] in ("--all", "all"):
        return paths
    target = " ".join(args)
    if target.isdigit():
        idx = int(target)
        if 0 <= idx < len(paths):
            return [paths[idx]]
        console.print(f"[red]Index out of range: {idx} (0-{len(paths) - 1})[/red]")
        sys.exit(1)
    matches = [p for p in paths if target.lower() in p.name.lower()]
    if len(matches) == 1:
        return matches
    if len(matches) > 1:
        console.print(f"[red]Multiple JSON matches for '{target}'.[/red]")
        sys.exit(1)
    console.print(f"[red]No parsed JSON matching: {target}[/red]")
    sys.exit(1)


def _parse_extract_args(args: list[str]) -> tuple[list[Path], bool, bool]:
    """Return (selected_paths, snapshot_before_run, include_qa)."""
    snapshot = False
    include_qa = True
    selected: list[Path] | None = None
    indices: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--snapshot":
            snapshot = True
            i += 1
        elif a == "--mgmt-only":
            include_qa = False
            i += 1
        elif a == "--include-qa":
            include_qa = True
            i += 1
        elif a == "--limit" and i + 1 < len(args):
            n = int(args[i + 1])
            selected = _get_parsed_json_paths()[:n]
            i += 2
        elif a in ("--all", "all"):
            selected = _get_parsed_json_paths()
            i += 1
        else:
            indices.append(a)
            i += 1

    if selected is None:
        paths = _get_parsed_json_paths()
        selected = []
        for a in indices:
            if a.isdigit():
                idx = int(a)
                if 0 <= idx < len(paths):
                    selected.append(paths[idx])
            else:
                matches = [p for p in paths if a.lower() in p.name.lower()]
                if matches:
                    selected.append(matches[0])
    return selected, snapshot, include_qa


def cmd_extract(args: list[str]) -> None:
    """Extract forward-looking claims from parsed JSON (Phase 3).

    Usage:
      extract 0           # one transcript
      extract --limit 3   # first 3 parsed files (chronological)
      extract --all       # all parsed files
      extract --snapshot --limit 3   # backup extractions/ before run
    """
    from src.agents.claim_extractor import extract_claims_from_transcript, extracted_items_to_claims
    from src.ingestion.parsed_loader import load_parsed_transcript, transcript_id_from_path
    from src.models.schema import ClaimSubtype
    from src.store.claims import export_claims_json, snapshot_extractions_dir
    from src.llm.azure import get_extraction_deployment
    from src.stats.reports import write_extraction_stats

    if not args:
        console.print(
            "[yellow]Usage:[/yellow]\n"
            "  [cyan]python -m src.main extract 0[/cyan]\n"
            "  [cyan]python -m src.main extract --limit 3[/cyan]\n"
            "  [cyan]python -m src.main extract --snapshot --limit 3[/cyan]\n"
            "  [cyan]python -m src.main extract --all[/cyan]\n"
        )
        return

    selected, do_snapshot, include_qa = _parse_extract_args(args)

    if not selected:
        console.print("[red]No transcripts selected.[/red]")
        return

    scope = "mgmt + Q&A" if include_qa else "prepared remarks only"
    if do_snapshot:
        snap = snapshot_extractions_dir()
        if snap:
            console.print(f"[dim]Snapshot saved: [cyan]{snap}[/cyan][/dim]")

    model = get_extraction_deployment()
    temp = os.getenv("AZURE_CHAT_TEMPERATURE", "0")
    seed = os.getenv("AZURE_CHAT_SEED", "")
    console.print(
        f"\n[bold]Extracting claims[/bold] model=[cyan]{model}[/cyan] "
        f"scope=[cyan]{scope}[/cyan] temp={temp} seed={seed or 'none'}\n"
    )

    extraction_rows = []
    for json_path in selected:
        tid = transcript_id_from_path(json_path)
        with console.status(f"Extracting {tid}..."):
            parsed = load_parsed_transcript(json_path)
            items, usage = extract_claims_from_transcript(
                parsed, tid, include_qa=include_qa
            )
            claims = extracted_items_to_claims(
                items, tid, parsed.metadata.transcript_date
            )
            export_path = export_claims_json(claims, tid)

        quant = sum(1 for c in claims if c.claim_subtype == ClaimSubtype.QUANTITATIVE)
        console.print(
            f"  [green]OK[/green] {tid}: {len(claims)} claims "
            f"(quant={quant}, qual={len(claims) - quant}) -> {export_path.name}"
        )
        extraction_rows.append(
            {
                "transcript_id": tid,
                "transcript_date": str(parsed.metadata.transcript_date),
                "claims_extracted": len(claims),
                "quantitative": quant,
                "qualitative": len(claims) - quant,
                "model": usage["model"],
                "tokens_in": usage["tokens_in"],
                "tokens_out": usage["tokens_out"],
            }
        )

    stats_path = write_extraction_stats(extraction_rows)
    console.print(f"\n[dim]Extraction stats: [cyan]{stats_path}[/cyan][/dim]\n")


def cmd_compare_extract(args: list[str]) -> None:
    """Compare two extraction run folders (data/stats/extractions vs a snapshot).

    Usage:
      compare-extract data/stats/runs/20260519_183342/extractions data/stats/extractions
      compare-extract runs/20260519_183342 runs/20260519_183702
    """
    from src.stats.extract_compare import compare_extraction_dirs, format_diff_report

    if len(args) < 2:
        console.print(
            "[yellow]Usage:[/yellow]\n"
            "  [cyan]python -m src.main compare-extract <dir_A> <dir_B>[/cyan]\n\n"
            "Snapshots live under [cyan]data/stats/runs/<timestamp>/extractions[/cyan]\n"
            "Use [cyan]extract --snapshot[/cyan] before re-running to keep run A.\n"
        )
        return

    dir_a = Path(args[0])
    dir_b = Path(args[1])
    if dir_a.name != "extractions" and (dir_a / "extractions").exists():
        dir_a = dir_a / "extractions"
    if dir_b.name != "extractions" and (dir_b / "extractions").exists():
        dir_b = dir_b / "extractions"

    diffs = compare_extraction_dirs(dir_a, dir_b)
    report = format_diff_report(diffs, dir_a, dir_b)
    out = Path("data/stats/extract_compare_report.txt")
    out.write_text(report, encoding="utf-8")
    console.print(report)
    console.print(f"\n[dim]Report saved: [cyan]{out}[/cyan][/dim]")


def cmd_stats(args: list[str]) -> None:
    """Regenerate parse stats under data/stats/."""
    from src.stats.reports import write_parse_stats

    path = write_parse_stats()
    console.print(f"[bold]Stats written:[/bold] [cyan]{path}[/cyan]")


def cmd_run(args: list[str]) -> None:
    """Walk-forward: extract new (mgmt+Q&A), resolve prior open, export 3 corpus files.

    Usage:
      run --limit 7
      run --limit 7 --extract-only   # skip resolution pass (faster)
      run --limit 5 --eval           # pipeline then extraction+resolution eval (LLM judge)
      run --limit 5 --extract-only --eval-extract   # extract only, then extraction eval
      run --all
    """
    from src.pipeline.orchestrator import run_walk_forward

    selected: list[Path] | None = None
    extract_only = False
    run_eval_after = False
    eval_extract_only = False
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            selected = _get_parsed_json_paths()[: int(args[i + 1])]
            i += 2
        elif args[i] == "--extract-only":
            extract_only = True
            i += 1
        elif args[i] == "--eval":
            run_eval_after = True
            i += 1
        elif args[i] == "--eval-extract":
            run_eval_after = True
            eval_extract_only = True
            i += 1
        elif args[i] in ("--all", "all"):
            selected = _get_parsed_json_paths()
            i += 1
        else:
            i += 1

    if not selected:
        console.print(
            "[yellow]Usage:[/yellow] [cyan]python -m src.main run --limit 7[/cyan]\n"
        )
        return

    label = f"Rockwool International, {len(selected)} transcripts"
    mode = "extract only" if extract_only else "extract then resolve"
    console.print(
        f"\n[bold]Walk-forward pipeline[/bold] transcripts={len(selected)} "
        f"(mgmt + Q&A, {mode})\n"
        f"[dim]Q1 earnings call ~50k chars → often 2–5 min per LLM step. Watch progress below.[/dim]\n"
    )

    def _log(msg: str) -> None:
        console.print(msg)

    state = run_walk_forward(
        selected,
        corpus_label=label,
        extract_only=extract_only,
        log=_log,
    )
    p1, p2, p3 = state.export_three_files(label)

    open_n = sum(1 for r in state.resolutions.values() if r.status == "open")
    console.print(
        f"\n[green]Done[/green] claims={len(state.claims)} threads={len(state.threads)} "
        f"open={open_n}\n"
        f"  [cyan]{p1}[/cyan]\n"
        f"  [cyan]{p2}[/cyan]\n"
        f"  [cyan]{p3}[/cyan]\n"
    )

    if run_eval_after:
        console.print("\n[bold]Running eval (LLM judge)...[/bold]\n")
        if eval_extract_only or extract_only:
            cmd_eval_extract([])
        else:
            _run_eval_bundle(_EvalPaths())


def cmd_restore(args: list[str]) -> None:
    console.print("[yellow]Restore not yet implemented — coming in Phase 4[/yellow]")


def cmd_check(args: list[str]) -> None:
    console.print("[yellow]Consistency checks not yet implemented[/yellow]")


def cmd_status(args: list[str]) -> None:
    """Show parsed transcript and claim corpus counts."""
    from src.analytics.corpus_loader import corpus_available, load_corpus

    parsed = _get_parsed_json_paths()
    console.print(f"[bold]Parsed transcripts:[/bold] {len(parsed)} in data/parsed/")
    if not corpus_available():
        console.print("[yellow]No claim corpus at data/claims/ — run pipeline.[/yellow]")
        return
    bundle = load_corpus()
    open_n = sum(1 for c in bundle.enriched if c.status == "open")
    console.print(
        f"[bold]Claim corpus:[/bold] {len(bundle.enriched)} claims, "
        f"{bundle.claims_threads.total_threads} threads, {open_n} open"
    )


def cmd_rebuild_trace(args: list[str]) -> None:
    """Rebuild thread traces and resolved_at fields from step snapshots (no LLM)."""
    from src.analytics.corpus_loader import load_corpus
    from src.pipeline.corpus_store import PipelineState

    from src.analytics.corpus_loader import corpus_available

    if not corpus_available():
        console.print("[red]No corpus at data/claims — run pipeline first.[/red]")
        return

    bundle = load_corpus()
    state = PipelineState()
    state.claims = list(bundle.claims_made.claims)
    for cw in bundle.claims_with_resolutions.claims:
        state.resolutions[cw.claim_id] = cw.resolution
    state.threads = dict(bundle.thread_by_id)
    max_id = 0
    for c in state.claims:
        if c.claim_id.startswith("RKW-"):
            try:
                max_id = max(max_id, int(c.claim_id.split("-")[1]))
            except ValueError:
                pass
    state._counter = max_id

    console.print("[bold]Rebuilding thread traces from step snapshots...[/bold]")
    state.rebuild_threads()
    p1, p2, p3 = state.export_three_files(bundle.corpus_label)
    console.print(
        f"[green]Done[/green]\n"
        f"  [cyan]{p1}[/cyan]\n"
        f"  [cyan]{p2}[/cyan]\n"
        f"  [cyan]{p3}[/cyan]\n"
    )


def cmd_dashboard(args: list[str]) -> None:
    """Launch Streamlit analytics dashboard."""
    import subprocess

    root = Path(__file__).resolve().parents[1]
    dashboard = root.parent / "app" / "dashboard.py"
    if not dashboard.exists():
        dashboard = Path("app/dashboard.py")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(dashboard),
        "--server.headless",
        "true",
        *args,
    ]
    console.print(f"[dim]Starting dashboard: {' '.join(cmd)}[/dim]\n")
    raise SystemExit(subprocess.call(cmd))


@dataclass
class _EvalPaths:
    similarity: float = 0.88
    transcript_filter: list[str] | None = None
    gold_dir: Path = Path("data/eval/gold/extraction")
    claims_made: Path = Path("data/claims/claims_made.json")
    gold_resolution: Path = Path("data/eval/gold/resolution/checkpoint_after_04.json")
    predictions: Path = Path(
        "data/claims/steps/04_2022-02-10_earnings_call_Q4/claims_with_resolutions.json"
    )
    parsed_dir: Path = Path("data/parsed")


def _parse_eval_args(args: list[str]) -> _EvalPaths:
    cfg = _EvalPaths()
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--only" and i + 1 < len(args):
            cfg.transcript_filter = [args[i + 1]]
            i += 1
        elif a == "--similarity" and i + 1 < len(args):
            cfg.similarity = float(args[i + 1])
            i += 1
        elif a == "--gold-dir" and i + 1 < len(args):
            cfg.gold_dir = Path(args[i + 1])
            i += 1
        elif a == "--claims-made" and i + 1 < len(args):
            cfg.claims_made = Path(args[i + 1])
            i += 1
        elif a == "--gold" and i + 1 < len(args):
            cfg.gold_resolution = Path(args[i + 1])
            i += 1
        elif a == "--predictions" and i + 1 < len(args):
            cfg.predictions = Path(args[i + 1])
            i += 1
        elif a == "--parsed-dir" and i + 1 < len(args):
            cfg.parsed_dir = Path(args[i + 1])
            i += 1
        else:
            console.print(f"[yellow]Unknown arg: {a}[/yellow]")
        i += 1
    return cfg


def _run_eval_bundle(cfg: _EvalPaths) -> Path:
    """Compare data/eval gold vs data/claims predictions; LLM judge + P/R."""
    from src.eval.extraction_eval import format_extraction_report, run_extraction_eval
    from src.eval.resolution_eval import format_resolution_report, run_resolution_eval
    from src.eval.summary import (
        build_eval_results,
        format_eval_results,
        write_eval_results,
    )

    ext_report = run_extraction_eval(
        gold_dir=cfg.gold_dir,
        claims_made_path=cfg.claims_made,
        parsed_dir=cfg.parsed_dir,
        similarity=cfg.similarity,
        transcript_filter=cfg.transcript_filter,
    )
    console.print(format_extraction_report(ext_report))
    console.print("")

    res_report = run_resolution_eval(
        gold_resolution_path=cfg.gold_resolution,
        gold_extraction_dir=cfg.gold_dir,
        predictions_path=cfg.predictions,
        parsed_dir=cfg.parsed_dir,
        similarity=cfg.similarity,
    )
    console.print(format_resolution_report(res_report, similarity=cfg.similarity))

    results = build_eval_results(
        ext_report,
        res_report,
        similarity=cfg.similarity,
        predictions_path=str(cfg.claims_made),
        resolution_predictions_path=str(cfg.predictions),
    )
    json_path, txt_path = write_eval_results(results)
    console.print("\n" + format_eval_results(results))
    console.print(f"\n[bold green]Results JSON:[/bold green] [cyan]{json_path}[/cyan]")
    console.print(f"[dim]Results text: {txt_path}[/dim]")
    return json_path


def cmd_eval(args: list[str]) -> None:
    """Eval both agents: gold (data/eval) vs pipeline (data/claims) → data/eval/results.json.

    Usage:
      python -m src.main eval
    """
    cfg = _parse_eval_args(args)
    console.print(
        Panel(
            f"gold       = data/eval/gold\n"
            f"claims     = {cfg.claims_made}\n"
            f"res preds  = {cfg.predictions}\n"
            f"output     = data/eval/results.json\n"
            f"LLM judge  = Azure GPT-4o",
            title="[bold]eval[/bold] — extraction + resolution",
            border_style="cyan",
        )
    )
    _run_eval_bundle(cfg)


def cmd_eval_extract(args: list[str]) -> None:
    """Evaluate pipeline extraction against gold (LLM judge on matched pairs).

    Usage:
      python -m src.main eval-extract
      python -m src.main eval-extract --only 00_2021-05-20_earnings_call_Q1
      python -m src.main eval-extract --similarity 0.88
    """
    from src.eval.extraction_eval import (
        format_extraction_report,
        run_extraction_eval,
        write_extraction_report,
    )

    cfg = _parse_eval_args(args)
    console.print(
        Panel(
            f"method     = LLM judge\n"
            f"gold_dir   = {cfg.gold_dir}\n"
            f"claims     = {cfg.claims_made}\n"
            f"similarity = {cfg.similarity}\n"
            f"only       = {cfg.transcript_filter or 'all'}",
            title="[bold]eval-extract[/bold]",
            border_style="cyan",
        )
    )

    report = run_extraction_eval(
        gold_dir=cfg.gold_dir,
        claims_made_path=cfg.claims_made,
        parsed_dir=cfg.parsed_dir,
        similarity=cfg.similarity,
        transcript_filter=cfg.transcript_filter,
    )
    console.print(format_extraction_report(report))
    report_path, results_path = write_extraction_report(report)
    console.print(f"\n[dim]Report: [cyan]{report_path}[/cyan]  JSON: [cyan]{results_path}[/cyan][/dim]")
    console.print("[dim]Tip: run [cyan]python -m src.main eval[/cyan] → data/eval/results.json[/dim]")


def cmd_eval_resolve(args: list[str]) -> None:
    """Evaluate pipeline resolution against gold checkpoint (LLM judge).

    Usage:
      python -m src.main eval-resolve
      python -m src.main eval-resolve --similarity 0.88
    """
    from src.eval.resolution_eval import (
        format_resolution_report,
        run_resolution_eval,
        write_resolution_report,
    )

    cfg = _parse_eval_args(args)
    console.print(
        Panel(
            f"method     = LLM judge\n"
            f"gold       = {cfg.gold_resolution}\n"
            f"predictions= {cfg.predictions}\n"
            f"similarity = {cfg.similarity}",
            title="[bold]eval-resolve[/bold]",
            border_style="cyan",
        )
    )

    report = run_resolution_eval(
        gold_resolution_path=cfg.gold_resolution,
        gold_extraction_dir=cfg.gold_dir,
        predictions_path=cfg.predictions,
        parsed_dir=cfg.parsed_dir,
        similarity=cfg.similarity,
    )
    console.print(format_resolution_report(report, similarity=cfg.similarity))
    report_path, results_path = write_resolution_report(
        report, similarity=cfg.similarity
    )
    console.print(f"\n[dim]Report: [cyan]{report_path}[/cyan]  JSON: [cyan]{results_path}[/cyan][/dim]")
    console.print("[dim]Tip: run [cyan]python -m src.main eval[/cyan] → data/eval/results.json[/dim]")


COMMANDS = {
    "list": cmd_list,
    "inspect": cmd_inspect,
    "parse": cmd_parse,
    "extract": cmd_extract,
    "compare-extract": cmd_compare_extract,
    "stats": cmd_stats,
    "run": cmd_run,
    "restore": cmd_restore,
    "check": cmd_check,
    "status": cmd_status,
    "dashboard": cmd_dashboard,
    "rebuild-trace": cmd_rebuild_trace,
    "eval": cmd_eval,
    "eval-extract": cmd_eval_extract,
    "eval-resolve": cmd_eval_resolve,
}


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] not in COMMANDS:
        console.print(
            Panel(
                "\n".join(
                    f"  [cyan]python -m src.main {cmd}[/cyan]"
                    for cmd in COMMANDS
                ),
                title="[bold]ClaimWatch CLI[/bold]",
                border_style="green",
            )
        )
        sys.exit(0)

    cmd = argv[0]
    COMMANDS[cmd](argv[1:])


if __name__ == "__main__":
    main()
