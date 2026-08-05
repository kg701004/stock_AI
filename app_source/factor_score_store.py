"""SQLite-backed factor scores, replacing the hardcoded 2-stock sample CSV.

`technical` is intentionally never stored here -- it is always derived live
from local daily-bar history via technical_factor.technical_factor_score(),
so there is only ever one source of truth for that factor (see the earlier
digest/weight_source fix in weighted_analysis.py for why that matters).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Mapping

from database_utils import database_connection
from technical_factor import technical_factor_score
from weighted_analysis import AnalysisInput, FACTOR_NAMES

MANUAL_FACTOR_NAMES = tuple(name for name in FACTOR_NAMES if name != "technical")

_COLUMNS = ("symbol", "as_of", *MANUAL_FACTOR_NAMES, "risk_score", "notes_json")

DEFAULT_MANUAL_FACTOR_SCORE = 50.0
DEFAULT_RISK_SCORE = 30.0
SEEDED_NOTE_KEY = "_seeded"
SEEDED_NOTE_TEXT = "新增時自動以預設值評分，尚未經人工確認；請至「個股評分輸入」複核後再次儲存。"


def ensure_schema(connection) -> None:
    factor_columns = ", ".join(f"{name} REAL NOT NULL" for name in MANUAL_FACTOR_NAMES)
    connection.execute(f"""
        CREATE TABLE IF NOT EXISTS factor_scores (
            symbol TEXT NOT NULL, as_of TEXT NOT NULL,
            {factor_columns},
            risk_score REAL NOT NULL, notes_json TEXT NOT NULL,
            PRIMARY KEY (symbol, as_of)
        )
    """)


def save_factor_scores(
    database: Path, symbol: str, as_of: datetime,
    factors: Mapping[str, float], risk_score: float, notes: Mapping[str, str],
) -> None:
    """Validate and upsert one symbol's manually-entered factors for one as_of timestamp."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    if set(factors) != set(MANUAL_FACTOR_NAMES):
        missing = set(MANUAL_FACTOR_NAMES) - set(factors)
        extra = set(factors) - set(MANUAL_FACTOR_NAMES)
        raise ValueError(f"factors must contain exactly the manual factors; missing={sorted(missing)}, extra={sorted(extra)}")
    for name, value in factors.items():
        if not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError(f"factor {name} must be from 0 to 100")
    if not isinstance(risk_score, (int, float)) or not 0 <= risk_score <= 100:
        raise ValueError("risk_score must be from 0 to 100")
    database.parent.mkdir(parents=True, exist_ok=True)
    values = [symbol, as_of.isoformat(), *(factors[name] for name in MANUAL_FACTOR_NAMES), risk_score, json.dumps(dict(notes), ensure_ascii=False)]
    with database_connection(database) as connection:
        ensure_schema(connection)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        connection.execute(f"INSERT OR REPLACE INTO factor_scores({', '.join(_COLUMNS)}) VALUES ({placeholders})", values)


def seed_default_factor_scores(decision_database: Path, history_database: Path, symbol: str, as_of: datetime) -> bool:
    """If this symbol has no saved factor score yet, save one now so a
    freshly tracked stock isn't stuck at "待輸入現價／評分" until the user
    separately visits 個股評分輸入 -- global_risk/liquidity get their real
    VIX/volume-derived suggestion (same as that screen would show), the
    genuinely-subjective factors get the neutral default (50), and the row
    is tagged SEEDED_NOTE_KEY so it can never be mistaken for a factor set a
    person actually reviewed. Never overwrites an existing score. Returns
    whether a row was actually seeded.
    """
    if load_symbol_factor_scores(decision_database, symbol) is not None:
        return False
    from dividend_adjustment import events_factor_score
    from sentiment_fear import global_risk_factor_score
    from fundamentals_data import fundamentals_factor_score
    from market_breadth import market_breadth_factor_score, sector_rotation_factor_score
    from technical_factor import liquidity_factor_score
    from valuation_data import valuation_factor_score
    factors = {name: DEFAULT_MANUAL_FACTOR_SCORE for name in MANUAL_FACTOR_NAMES}
    for name, (score, _note) in {
        "global_risk": global_risk_factor_score(history_database),
        "liquidity": liquidity_factor_score(history_database, symbol),
        "valuation": valuation_factor_score(history_database, symbol),
        "fundamentals": fundamentals_factor_score(history_database, symbol),
        "market_breadth": market_breadth_factor_score(history_database),
        "sector_rotation": sector_rotation_factor_score(history_database, symbol),
        "events": events_factor_score(history_database, symbol, as_of.date()),
    }.items():
        if score is not None:
            factors[name] = score
    save_factor_scores(decision_database, symbol, as_of, factors, DEFAULT_RISK_SCORE, {SEEDED_NOTE_KEY: SEEDED_NOTE_TEXT})
    return True


def load_symbol_factor_scores(database: Path, symbol: str) -> tuple[dict[str, float], float, dict[str, str]] | None:
    """Return the latest saved (manual_factors, risk_score, notes) for one symbol, or None."""
    with database_connection(database) as connection:
        ensure_schema(connection)
        row = connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM factor_scores WHERE symbol = ? ORDER BY as_of DESC LIMIT 1",
            (symbol,),
        ).fetchone()
    if row is None:
        return None
    manual_values = dict(zip(MANUAL_FACTOR_NAMES, row[2:2 + len(MANUAL_FACTOR_NAMES)]))
    risk_score = row[2 + len(MANUAL_FACTOR_NAMES)]
    notes = json.loads(row[3 + len(MANUAL_FACTOR_NAMES)])
    return manual_values, risk_score, notes


def load_all_current_assessments(decision_database: Path, history_database: Path) -> dict[str, AnalysisInput]:
    """Return the latest manually-entered row per symbol, merged with the auto-computed technical factor.

    This is the direct replacement for the old `{row.symbol: row for row in
    load_factor_csv(...)}` pattern that was wired to the 2-stock sample CSV.
    """
    with database_connection(decision_database) as connection:
        ensure_schema(connection)
        rows = connection.execute(f"""
            SELECT {', '.join(_COLUMNS)} FROM factor_scores AS current_row
            WHERE as_of = (SELECT MAX(as_of) FROM factor_scores WHERE symbol = current_row.symbol)
        """).fetchall()
    results: dict[str, AnalysisInput] = {}
    for row in rows:
        symbol, as_of_text = row[0], row[1]
        manual_values = dict(zip(MANUAL_FACTOR_NAMES, row[2:2 + len(MANUAL_FACTOR_NAMES)]))
        risk_score = row[2 + len(MANUAL_FACTOR_NAMES)]
        notes = json.loads(row[3 + len(MANUAL_FACTOR_NAMES)])
        technical_score, technical_note = technical_factor_score(history_database, symbol)
        factors = {**manual_values, "technical": 50.0 if technical_score is None else technical_score}
        notes = {**notes, "technical": technical_note}
        results[symbol] = AnalysisInput(symbol, datetime.fromisoformat(as_of_text), factors, risk_score, notes)
    return results
