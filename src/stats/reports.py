"""Write human-readable stats files under data/stats/."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.models.schema import ParsedTranscript


STATS_DIR = Path("data/stats")
PARSED_DIR = Path("data/parsed")


def _ensure_stats_dir() -> Path:
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    return STATS_DIR


def _format_table(
    title: str,
    headers: list[str],
    rows: list[list[str]],
    widths: list[int] | None = None,
) -> str:
    """ASCII table similar to Rich output in summary.txt."""
    if not widths:
        widths = [max(len(h), max((len(r[i]) for r in rows), default=0)) for i, h in enumerate(headers)]

    def fmt_row(cells: list[str]) -> str:
        parts = [cells[i].ljust(widths[i]) for i in range(len(headers))]
        return "│ " + " │ ".join(parts) + " │"

    top = "╭" + "┬".join("─" * (w + 2) for w in widths) + "╮"
    sep = "├" + "┼".join("─" * (w + 2) for w in widths) + "┤"
    bot = "╰" + "┴".join("─" * (w + 2) for w in widths) + "╯"
    header_line = fmt_row(headers)

    lines = [title.center(sum(widths) + 3 * len(headers) + 1), top, header_line, sep]
    for row in rows:
        lines.append(fmt_row(row))
    lines.append(bot)
    return "\n".join(lines)


def collect_parse_stats(parsed_dir: Path | None = None) -> list[dict]:
    """Build parse stats from data/parsed JSON files."""
    parsed_dir = parsed_dir or PARSED_DIR
    rows = []
    for i, path in enumerate(sorted(parsed_dir.glob("*.json"))):
        data = json.loads(path.read_text(encoding="utf-8"))
        parsed = ParsedTranscript.model_validate(data)
        meta = parsed.metadata
        mgmt = sum(1 for t in parsed.speaker_turns if t.is_management)
        rows.append(
            {
                "index": i,
                "transcript_id": path.stem,
                "date": str(meta.transcript_date),
                "event_type": meta.event_type.value,
                "quarter": meta.quarter or "",
                "year": meta.year,
                "pages": parsed.total_pages or 0,
                "turns": len(parsed.speaker_turns),
                "mgmt_turns": mgmt,
                "qa_turns": len(parsed.speaker_turns) - mgmt,
                "file": path.name,
            }
        )
    return rows


def write_parse_stats(parsed_dir: Path | None = None) -> Path:
    stats_dir = _ensure_stats_dir()
    rows = collect_parse_stats(parsed_dir)

    totals = {
        "transcripts": len(rows),
        "total_turns": sum(r["turns"] for r in rows),
        "total_mgmt_turns": sum(r["mgmt_turns"] for r in rows),
        "total_pages": sum(r["pages"] for r in rows),
    }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": totals,
        "transcripts": rows,
    }
    (stats_dir / "parse_summary.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    table_rows = [
        [
            str(r["index"]),
            r["date"],
            r["event_type"],
            str(r["pages"]),
            str(r["turns"]),
            str(r["mgmt_turns"]),
            r["file"],
        ]
        for r in rows
    ]
    table = _format_table(
        "Parse Results",
        ["#", "Date", "Event", "Pages", "Turns", "Mgmt", "File"],
        table_rows,
        widths=[4, 12, 22, 6, 6, 6, 42],
    )
    txt = (
        f"Generated: {payload['generated_at']}\n"
        f"TOTAL transcripts={totals['transcripts']}  turns={totals['total_turns']}  "
        f"mgmt_turns={totals['total_mgmt_turns']}  pages={totals['total_pages']}\n\n"
        f"{table}\n"
    )
    out = stats_dir / "parse_summary.txt"
    out.write_text(txt, encoding="utf-8")
    return out


def write_extraction_stats(extraction_rows: list[dict]) -> Path:
    stats_dir = _ensure_stats_dir()
    totals = {
        "transcripts": len(extraction_rows),
        "total_claims": sum(r.get("claims_extracted", 0) for r in extraction_rows),
    }
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": totals,
        "transcripts": extraction_rows,
    }
    (stats_dir / "extraction_summary.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    table_rows = [
        [
            r.get("transcript_id", ""),
            r.get("transcript_date", ""),
            str(r.get("claims_extracted", 0)),
            str(r.get("quantitative", 0)),
            str(r.get("qualitative", 0)),
            r.get("model", ""),
        ]
        for r in extraction_rows
    ]
    table = _format_table(
        "Extraction Results (Claims)",
        ["Transcript ID", "Date", "Claims", "Quant", "Qual", "Model"],
        table_rows,
        widths=[36, 12, 8, 8, 8, 14],
    )
    txt = (
        f"Generated: {payload['generated_at']}\n"
        f"TOTAL transcripts={totals['transcripts']}  claims={totals['total_claims']}\n\n"
        f"{table}\n"
    )
    out = stats_dir / "extraction_summary.txt"
    out.write_text(txt, encoding="utf-8")
    return out


def refresh_all_stats() -> dict[str, str]:
    """Regenerate parse stats under data/stats/."""
    return {"parse": str(write_parse_stats())}
