"""Time-decay rules for open claims (walk-forward)."""

from __future__ import annotations

import re
from datetime import date

from src.models.corpus import ClaimMade

# Days after timeframe end before auto-stale
DEFAULT_GRACE_DAYS = 120


def _parse_timeframe_end(timeframe: str, claim_date: date) -> date | None:
    """Best-effort end date for a claim horizon string."""
    t = timeframe.strip().lower()
    if not t or t in ("near-term", "multi-year", "future", "ongoing"):
        return None

    m = re.search(r"fy\s*(\d{4})", t)
    if m:
        return date(int(m.group(1)), 12, 31)

    m = re.search(r"q([1-4])\s*(\d{4})", t)
    if m:
        q, y = int(m.group(1)), int(m.group(2))
        month = q * 3
        return date(y, month, 28 if month == 2 else 30)

    m = re.search(r"h([12])\s*(\d{4})", t)
    if m:
        half, y = int(m.group(1)), int(m.group(2))
        return date(y, 6 if half == 1 else 12, 30)

    m = re.search(r"\b(20\d{2})\b", t)
    if m:
        y = int(m.group(1))
        if "early" in t:
            return date(y, 6, 30)
        if "late" in t:
            return date(y, 12, 31)
        return date(y, 12, 31)

    m = re.search(r"(\d{4})", t)
    if m:
        return date(int(m.group(1)), 12, 31)

    # Relative to claim year
    if "next year" in t:
        return date(claim_date.year + 1, 12, 31)

    return None


def expire_stale_open_claims(
    claims: list[ClaimMade],
    resolutions: dict,
    as_of: date,
    *,
    grace_days: int = DEFAULT_GRACE_DAYS,
) -> int:
    """
    Mark open claims past their horizon + grace as stale.
    Returns count expired.
    """
    from src.models.corpus import ClaimResolution

    expired = 0
    for c in claims:
        res = resolutions.get(c.claim_id)
        if not res or res.status != "open":
            continue
        end = _parse_timeframe_end(c.timeframe, c.date_made)
        if end is None:
            continue
        deadline = end.toordinal() + grace_days
        if as_of.toordinal() > deadline:
            res = resolutions[c.claim_id]
            res.status = "stale"
            res.resolution_notes = f"Auto-stale: horizon {c.timeframe} ended before {as_of}"
            if not res.resolved_at_date:
                res.resolved_at_date = as_of
            expired += 1
    return expired


def filter_claims_for_resolver(
    open_claims: list[ClaimMade],
    transcript_text: str,
    *,
    new_claims: list[ClaimMade] | None = None,
    max_claims: int = 35,
) -> list[ClaimMade]:
    """
    Pre-filter open claims to those plausibly relevant to this transcript.
    Always includes claims sharing a thread_id with any new claim from this step.
    """
    text = transcript_text.lower()
    new_thread_ids = {c.thread_id for c in (new_claims or []) if c.thread_id}

    scored: list[tuple[int, ClaimMade]] = []
    for c in open_claims:
        score = 0
        if c.thread_id and c.thread_id in new_thread_ids:
            score += 10
        subject_tokens = [w for w in re.split(r"[^a-z0-9]+", c.subject.lower()) if len(w) > 3]
        for tok in subject_tokens[:8]:
            if tok in text:
                score += 2
        if any(tok in text for tok in re.split(r"[^a-z0-9%€]+", c.target_value.lower()) if len(tok) > 2):
            score += 1
        if score > 0 or len(open_claims) <= max_claims:
            scored.append((score, c))

    if not scored:
        scored = [(0, c) for c in open_claims]

    scored.sort(key=lambda x: (-x[0], x[1].date_made), reverse=False)
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:max_claims]]
