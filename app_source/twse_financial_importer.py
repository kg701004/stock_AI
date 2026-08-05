"""Import official TWSE OpenAPI financial JSON, downloaded automatically or manually."""
from __future__ import annotations
import json
from pathlib import Path
from external_data_importers import MopsFinancial

def parse_profitability_json(payload: bytes, source: str = "TWSE OpenAPI t187ap17_L") -> list[MopsFinancial]:
    rows = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(rows, list): raise ValueError("TWSE payload must be a JSON array")
    result = []
    for row in rows:
        symbol = str(row.get("公司代號", "")).strip()
        try:
            year, quarter = int(str(row["年度"])), int(str(row["季別"]))
        except (KeyError, ValueError) as error: raise ValueError("TWSE row needs company code, fiscal year and quarter") from error
        if not (symbol.isdigit() and len(symbol) == 4 and 1 <= quarter <= 4): continue
        def number(*names: str) -> float | None:
            for name in names:
                raw = str(row.get(name, "")).replace(",", "").replace("%", "").strip()
                if raw and raw not in {"-", "--"}:
                    try: return float(raw)
                    except ValueError: raise ValueError(f"invalid {name} for {symbol}")
            return None
        result.append(MopsFinancial(symbol, year, quarter, number("營業收入(百萬元)", "營業收入"), None, number("毛利率(%)(營業毛利)/(營業收入)", "毛利率(%)"), number("營業利益率(%)(營業利益)/(營業收入)", "營業利益率(%)"), None, None, source))
    if not result: raise ValueError("TWSE JSON has no valid financial records")
    return result

def load_profitability_json(path: Path) -> list[MopsFinancial]:
    return parse_profitability_json(path.read_bytes(), f"TWSE manual JSON:{path.name}")
