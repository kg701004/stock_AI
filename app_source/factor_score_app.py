"""GUI screen for manually entering the non-technical factor scores.

`technical` is never edited here -- it is always auto-derived live from local
daily-bar history (technical_factor.py) and shown read-only for context.
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import ui_theme
from dividend_adjustment import events_factor_score
from external_data_importers import global_risk_factor_score
from factor_score_store import MANUAL_FACTOR_NAMES, SEEDED_NOTE_KEY, load_symbol_factor_scores, save_factor_scores
from fundamentals_data import fundamentals_factor_score
from market_breadth import market_breadth_factor_score, sector_rotation_factor_score
from security_catalog import resolve
from storage_paths import storage_paths
from technical_factor import liquidity_factor_score, technical_factor_score
from valuation_data import valuation_factor_score
from visualization import radar_chart, risk_gauge

MANUAL_FACTOR_LABELS = {
    "market_breadth": "市場廣度", "sector_rotation": "產業輪動", "fundamentals": "基本面",
    "institutional_flow": "法人動向", "derivatives": "衍生性商品", "global_risk": "全球風險",
    "sentiment": "情緒指標", "events": "事件風險", "liquidity": "流動性", "valuation": "評價",
}
FACTOR_LABELS = {**MANUAL_FACTOR_LABELS, "technical": "技術面"}

# Factors with a real local/live data source -- lookup_symbol() overwrites
# these with a fresh auto-suggestion every time instead of leaving whatever
# was last saved (the remaining MANUAL_FACTOR_NAMES stay genuinely manual:
# institutional_flow, derivatives, sentiment have no verified free data
# source yet). Each entry is a callable taking (history_database, symbol)
# and returning (score, note); market-wide ones (market_breadth) ignore symbol.
AUTO_SUGGESTED_FACTOR_SCORERS = {
    "global_risk": lambda database, symbol: global_risk_factor_score(database),
    "liquidity": liquidity_factor_score,
    "valuation": valuation_factor_score,
    "fundamentals": fundamentals_factor_score,
    "market_breadth": lambda database, symbol: market_breadth_factor_score(database),
    "sector_rotation": sector_rotation_factor_score,
    "events": lambda database, symbol: events_factor_score(database, symbol),
}


class FactorScoreApp(ttk.Frame):
    """Look up a symbol, review its auto-computed technical score, and enter the rest by hand."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12)
        self.symbol: str | None = None
        self._technical_score: float | None = None
        self.values = {name: tk.IntVar(value=50) for name in MANUAL_FACTOR_NAMES}
        self.risk_value = tk.IntVar(value=30)
        self._build()

    def _build(self) -> None:
        columns = ttk.Frame(self); columns.pack(fill="both", expand=True)
        left = ttk.Frame(columns); left.pack(side="left", fill="both", expand=True)
        self.chart_area = ttk.Frame(columns, width=440); self.chart_area.pack(side="left", fill="both", padx=(16, 0))

        lookup = ttk.Frame(left); lookup.pack(fill="x")
        ttk.Label(lookup, text="股票代號或名稱").pack(side="left")
        self.query = ttk.Entry(lookup, width=20); self.query.pack(side="left", padx=(4, 8))
        ttk.Button(lookup, text="查詢", command=self.lookup_symbol).pack(side="left")
        self.resolved = ttk.Label(lookup, text="尚未查詢股票", foreground="#555555"); self.resolved.pack(side="left", padx=(12, 0))

        ttk.Label(left, text="技術面（自動計算，唯讀）", font=("Microsoft JhengHei UI", 10, "bold")).pack(anchor="w", pady=(14, 2))
        self.technical_status = ttk.Label(left, text="請先查詢股票。", foreground="#555555", wraplength=460, justify="left")
        self.technical_status.pack(anchor="w")

        ttk.Label(left, text="其餘因子分數（0～100；標「自動建議」的已可依真實資料建議，其餘仍需自行判斷輸入）", font=("Microsoft JhengHei UI", 10, "bold")).pack(anchor="w", pady=(14, 2))
        grid = ttk.Frame(left); grid.pack(anchor="w", fill="x")
        for index, name in enumerate(MANUAL_FACTOR_NAMES):
            label = MANUAL_FACTOR_LABELS[name] + ("（自動建議）" if name in AUTO_SUGGESTED_FACTOR_SCORERS else "")
            ttk.Label(grid, text=label, width=18).grid(row=index, column=0, sticky="w", pady=2)
            ttk.Scale(grid, from_=0, to=100, orient="horizontal", variable=self.values[name], length=240).grid(row=index, column=1, padx=8)
            ttk.Label(grid, textvariable=self.values[name], width=4).grid(row=index, column=2)

        risk_row = ttk.Frame(left); risk_row.pack(anchor="w", pady=(10, 0))
        ttk.Label(risk_row, text="風險分數", width=14).pack(side="left")
        ttk.Scale(risk_row, from_=0, to=100, orient="horizontal", variable=self.risk_value, length=240).pack(side="left", padx=8)
        ttk.Label(risk_row, textvariable=self.risk_value, width=4).pack(side="left")

        button_row = ttk.Frame(left); button_row.pack(anchor="w", pady=(14, 0))
        ttk.Button(button_row, text="儲存評分", style="Primary.TButton", command=self.save).pack(side="left")
        ttk.Button(button_row, text="重設為中性（50）", command=self.reset_to_neutral).pack(side="left", padx=(6, 0))
        self.status = ttk.Label(left, text=""); self.status.pack(anchor="w", pady=4)

    def reset_to_neutral(self) -> None:
        """Quick escape hatch when starting a fresh assessment: every manual factor and risk back to the neutral midpoint."""
        for var in self.values.values():
            var.set(50)
        self.risk_value.set(30)
        self._render_charts()

    def _current_scores(self) -> dict[str, float]:
        return {**{name: float(var.get()) for name, var in self.values.items()}, "technical": self._technical_score if self._technical_score is not None else 50.0}

    def _render_charts(self) -> None:
        for child in self.chart_area.winfo_children():
            child.destroy()
        if self.symbol is None:
            return
        ttk.Label(self.chart_area, text=f"{self.symbol}．個股健診", font=("Microsoft JhengHei UI", 10, "bold")).pack(anchor="w")
        radar_holder = ttk.Frame(self.chart_area); radar_holder.pack(fill="both", expand=True)
        radar_chart(radar_holder, self._current_scores(), FACTOR_LABELS)
        gauge_holder = ttk.Frame(self.chart_area); gauge_holder.pack(fill="x")
        risk_gauge(gauge_holder, float(self.risk_value.get()), "風險分數")

    def lookup_symbol(self) -> None:
        query = self.query.get().strip()
        if not query:
            return
        try:
            symbol = resolve(storage_paths()["history_database"], query)
        except ValueError as error:
            messagebox.showerror("查詢失敗", str(error), parent=self.winfo_toplevel()); return
        self.symbol = symbol
        self.resolved.configure(text=f"已選定：{symbol}")
        score, note = technical_factor_score(storage_paths()["history_database"], symbol)
        self._technical_score = score
        self.technical_status.configure(text=note if score is None else f"分數 {score:.1f}。{note}")
        saved = load_symbol_factor_scores(storage_paths()["decision_database"], symbol)
        if saved is not None:
            manual_values, risk_score, notes = saved
            for name, value in manual_values.items():
                self.values[name].set(int(round(value)))
            self.risk_value.set(int(round(risk_score)))
            if SEEDED_NOTE_KEY in notes:
                status_text = "⚠ 目前是新增時自動帶入的預設評分，尚未經人工確認，請檢視後按「儲存評分」確認。"
            else:
                status_text = "已載入先前儲存的分數，可直接修改後再次儲存。"
        else:
            status_text = "此股票尚無已儲存的分數。"
        # Factors with a real local/live data source -- always overwrite with
        # a fresh suggestion rather than leaving a stale saved value or the
        # generic neutral default.
        suggestion_notes = []
        for name, scorer in AUTO_SUGGESTED_FACTOR_SCORERS.items():
            score_value, note = scorer(storage_paths()["history_database"], symbol)
            if score_value is not None:
                self.values[name].set(int(round(score_value))); suggestion_notes.append(note)
        self.status.configure(text=status_text + ("\n" + " ".join(suggestion_notes) if suggestion_notes else ""))
        self._render_charts()

    def save(self) -> None:
        if self.symbol is None:
            messagebox.showinfo("請先查詢", "請先查詢並選定一檔股票，再儲存分數。", parent=self.winfo_toplevel()); return
        try:
            save_factor_scores(
                storage_paths()["decision_database"], self.symbol, datetime.now().astimezone(),
                {name: float(var.get()) for name, var in self.values.items()}, float(self.risk_value.get()), {},
            )
            self.status.configure(text=f"{self.symbol} 的評分已儲存。下次重新分析時將套用。")
            self._render_charts()
        except ValueError as error:
            messagebox.showerror("儲存失敗", str(error), parent=self.winfo_toplevel())
