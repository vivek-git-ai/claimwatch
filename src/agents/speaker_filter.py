"""Identify management speakers (parser is_management flag is incomplete)."""

from __future__ import annotations

from src.models.schema import SpeakerTurn

_MGMT_NAME_PARTS = (
    "birgersson",
    "junge andersen",
    "kim junge",
    "vitale",
    "abbotts",
    "hansen",
    "munk hansen",
)

_MGMT_ROLE_KEYWORDS = (
    "chief executive",
    "ceo",
    "cfo",
    "chief financial",
    "president",
    "senior vice president",
    "svp",
    "director",
    "rockwool",
    "rock wool",
)


def is_management_turn(turn: SpeakerTurn) -> bool:
    if turn.speaker_name.strip().lower() == "operator":
        return False
    if turn.is_management:
        return True
    name = turn.speaker_name.lower()
    if any(part in name for part in _MGMT_NAME_PARTS):
        return True
    role = (turn.speaker_role or "").lower()
    if role and any(kw in role for kw in _MGMT_ROLE_KEYWORDS):
        if "analyst" not in role:
            return True
    return False
