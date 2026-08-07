"""Pure scheduling logic; GUI or a future EXE can call this without a service."""
from __future__ import annotations
from datetime import datetime, time
from update_manager import SCHEDULES

def _scheduled_time(source: str) -> time:
    """Map stable ASCII provider prefixes, avoiding display-text encoding issues."""
    if source.startswith("TWSE"): return time(16)
    if source.startswith("TPEx"): return time(16, 30)
    if source.startswith("TAIFEX"): return time(7)
    if source.startswith("VIX"): return time(7)
    # Not a market-hours job -- "due" the moment the app opens, any time of
    # day; due_sources' own completed_today check is what actually limits
    # this to at most once per real calendar day.
    if source.startswith(("GAP", "REVERSAL", "DRIFT", "MARKET_INDEX", "INSTITUTIONAL_FLOW", "MARGIN_BALANCE", "ARCHIVE")): return time(0)
    raise ValueError(f"unknown scheduled source: {source}")
def due_sources(now: datetime, completed_today: set[str]) -> list[str]:
    if now.tzinfo is None: raise ValueError("now needs timezone")
    return [source for source in SCHEDULES if source not in completed_today and now.time() >= _scheduled_time(source)]
