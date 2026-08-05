"""Per-owner, SQLite-persisted portfolio-risk options."""
from __future__ import annotations
import json
from pathlib import Path
from database_utils import database_connection

PROFILES = {
    "保守": {"maximum_position_weight_pct": 12, "maximum_sector_weight_pct": 25, "maximum_portfolio_beta": 0.9, "high_correlation_threshold": 0.65},
    "平衡": {"maximum_position_weight_pct": 20, "maximum_sector_weight_pct": 40, "maximum_portfolio_beta": 1.2, "high_correlation_threshold": 0.75},
    "積極": {"maximum_position_weight_pct": 30, "maximum_sector_weight_pct": 55, "maximum_portfolio_beta": 1.5, "high_correlation_threshold": 0.85},
}
DEFAULT_OPTIONS = {"enable_correlation": True, "enable_dynamic_beta": True, "enable_var_es": True, "enable_stress_test": True, "enable_rebalance": True, "enable_correlation_stress": True, "window_days": 250, "benchmark_symbol": "0050", "var_confidence_pct": 95, "correlation_stress_shock_pct": 50}

def load(database: Path, owner: str) -> dict[str, object]:
    with database_connection(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS portfolio_risk_preferences (owner TEXT PRIMARY KEY, profile TEXT NOT NULL, options_json TEXT NOT NULL)")
        row=connection.execute("SELECT profile, options_json FROM portfolio_risk_preferences WHERE owner=?",(owner,)).fetchone()
    if row is None: return {"profile":"平衡", **PROFILES["平衡"], **DEFAULT_OPTIONS}
    options=json.loads(row[1]); profile=row[0]
    # Mirror save()'s fallback: an unrecognized/custom profile still needs a
    # base for the four risk-rule fields, or callers get a KeyError whenever
    # those fields were never explicitly overridden.
    return {"profile":profile, **PROFILES.get(profile, PROFILES["平衡"]), **DEFAULT_OPTIONS, **options}

def save(database: Path, owner: str, profile: str, overrides: dict[str, object]) -> dict[str, object]:
    if profile not in {*PROFILES, "自訂"}: raise ValueError("unknown risk profile")
    value={"profile":profile, **(PROFILES.get(profile, PROFILES["平衡"])), **DEFAULT_OPTIONS, **overrides}
    if int(value["window_days"]) not in {60,120,250}: raise ValueError("window_days must be 60, 120 or 250")
    if not 80 <= int(value["var_confidence_pct"]) <= 99: raise ValueError("VaR confidence must be 80..99")
    if not 0 <= int(value["correlation_stress_shock_pct"]) <= 100: raise ValueError("correlation_stress_shock_pct must be 0..100")
    for name in ("maximum_position_weight_pct","maximum_sector_weight_pct","maximum_portfolio_beta","high_correlation_threshold"):
        if float(value[name]) <= 0: raise ValueError(f"{name} must be positive")
    database.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS portfolio_risk_preferences (owner TEXT PRIMARY KEY, profile TEXT NOT NULL, options_json TEXT NOT NULL)")
        connection.execute("INSERT OR REPLACE INTO portfolio_risk_preferences VALUES (?,?,?)",(owner,profile,json.dumps(overrides,ensure_ascii=False)))
    return value
