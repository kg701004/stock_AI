"""User-facing 1..10 judgement weights shared by every factor-based evaluation."""
from __future__ import annotations
import json
from pathlib import Path
from weighted_analysis import FACTOR_NAMES

DEFAULT_WEIGHT = 5
def default_weights() -> dict[str, int]: return {name: DEFAULT_WEIGHT for name in FACTOR_NAMES}
def load(path: Path) -> dict[str, int]:
    if not path.exists(): return default_weights()
    raw=json.loads(path.read_text(encoding="utf-8")); weights=raw.get("weights", raw)
    if set(weights) != set(FACTOR_NAMES): raise ValueError("judgement weights must contain every known factor")
    if any(not isinstance(value,int) or not 1 <= value <= 10 for value in weights.values()): raise ValueError("each judgement weight must be an integer from 1 to 10")
    return dict(weights)
def save(path: Path, weights: dict[str,int]) -> None:
    validated=load_from_values(weights); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps({"version":"1.0","weights":validated},ensure_ascii=False,indent=2),encoding="utf-8")
def load_from_values(weights: dict[str,int]) -> dict[str,int]:
    if set(weights) != set(FACTOR_NAMES) or any(not isinstance(value,int) or not 1 <= value <= 10 for value in weights.values()): raise ValueError("weights must be integers 1..10 for all factors")
    return dict(weights)
