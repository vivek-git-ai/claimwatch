"""Load parsed transcript JSON from data/parsed/."""

from __future__ import annotations

import json
from pathlib import Path

from src.models.schema import ParsedTranscript


def transcript_id_from_path(json_path: Path) -> str:
    return json_path.stem


def load_parsed_transcript(json_path: Path) -> ParsedTranscript:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return ParsedTranscript.model_validate(data)
