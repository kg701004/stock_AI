"""Adapters for manually imported, end-of-day factor-score CSV files."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from weighted_analysis import AnalysisInput, FACTOR_NAMES


REQUIRED_COLUMNS = {"symbol", "as_of", "risk_score", *FACTOR_NAMES}


def load_factor_csv(path: Path) -> list[AnalysisInput]:
    """Load normalized factor scores from UTF-8 CSV and preserve row explanations.

    Required factor columns use the 0--100 scale. Optional ``note_<factor>``
    columns become the auditable reason shown in the final assessment.
    """
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not REQUIRED_COLUMNS <= set(reader.fieldnames):
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            raise ValueError(f"CSV is missing required columns: {sorted(missing)}")
        entries: list[AnalysisInput] = []
        for row_number, row in enumerate(reader, start=2):
            try:
                factors = {factor: float(row[factor]) for factor in FACTOR_NAMES}
                notes = {factor: row.get(f"note_{factor}", "") for factor in FACTOR_NAMES if row.get(f"note_{factor}", "")}
                entries.append(AnalysisInput(row["symbol"], datetime.fromisoformat(row["as_of"]), factors, float(row["risk_score"]), notes))
            except (TypeError, ValueError) as error:
                raise ValueError(f"invalid row {row_number}: {error}") from error
    if not entries:
        raise ValueError("CSV contains no data rows")
    return entries
