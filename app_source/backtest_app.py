"""GUI screen for walk-forward validating the technical signal against real history.

Reuses technical_validation.py's leak-free validator, which already existed in
this codebase but had no GUI or usage entry point before this screen.

Scope, stated honestly: only the `technical` factor can be backtested this
way. The other 10 weighted_analysis factors have no historical time series --
factor_score_store only keeps each symbol's latest manually-entered snapshot,
not what the score was on any past date -- so there is no way to reconstruct
what the full 11-factor weighted score would have been historically. This
screen validates the technical signal alone, not the whole scoring system.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import ui_theme
from security_catalog import resolve
from storage_paths import storage_paths
from technical_factor import load_adjusted_bars
from technical_validation import validate


class BacktestApp(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12)
        ttk.Label(
            self,
            text="本回測只驗證「技術面」單一訊號(均線排列、RSI、MACD、突破確認)是否真的領先股價，"
                 "不是完整 11 因子加權分數——基本面、法人動向等其餘因子沒有歷史時間序列資料，無法回測整套評分系統。"
                 "樣本外(後 30%)結果比樣本內更有參考價值，因為樣本內容易高估。"
                 "報酬計算假設訊號當天收盤價即可成交、無滑價，且未扣除手續費與證交稅，實際可達成報酬會更低。",
            foreground=ui_theme.WARNING, wraplength=780, justify="left",
        ).pack(anchor="w")

        lookup = ttk.Frame(self); lookup.pack(fill="x", pady=(10, 0))
        ttk.Label(lookup, text="股票代號或名稱").pack(side="left")
        self.query = ttk.Entry(lookup, width=18); self.query.pack(side="left", padx=(4, 10))
        ttk.Label(lookup, text="分數門檻").pack(side="left")
        self.threshold = ttk.Spinbox(lookup, from_=50, to=90, width=5); self.threshold.pack(side="left", padx=(4, 10)); self.threshold.set(65)
        ttk.Label(lookup, text="持有天數").pack(side="left")
        self.holding_days = ttk.Spinbox(lookup, from_=1, to=60, width=5); self.holding_days.pack(side="left", padx=(4, 10)); self.holding_days.set(5)
        ttk.Button(lookup, text="執行回測", command=self.run_backtest).pack(side="left")

        self.status = ttk.Label(self, foreground="#555555", wraplength=780, justify="left"); self.status.pack(anchor="w", pady=(12, 0))

        columns = ttk.Frame(self); columns.pack(fill="x", pady=(14, 0))
        self.in_sample_label = ttk.Label(columns, text="", justify="left"); self.in_sample_label.pack(side="left", padx=(0, 50), anchor="n")
        self.out_sample_label = ttk.Label(columns, text="", justify="left", font=("Microsoft JhengHei UI", 10, "bold")); self.out_sample_label.pack(side="left", anchor="n")

    def run_backtest(self) -> None:
        query = self.query.get().strip()
        if not query:
            return
        try:
            symbol = resolve(storage_paths()["history_database"], query)
        except ValueError as error:
            messagebox.showerror("查詢失敗", str(error), parent=self.winfo_toplevel()); return
        try:
            bars = load_adjusted_bars(storage_paths()["history_database"], symbol)
            in_sample, out_sample = validate(bars, threshold=float(self.threshold.get()), holding_days=int(self.holding_days.get()))
        except ValueError as error:
            self.status.configure(text=f"{symbol}：無法回測——{error}")
            self.in_sample_label.configure(text=""); self.out_sample_label.configure(text="")
            return
        self.status.configure(text=f"{symbol}：分數門檻 {self.threshold.get()}、訊號後持有 {self.holding_days.get()} 天，走勢驗證結果如下（時間序列切分，樣本外未使用樣本內資料）：")
        self.in_sample_label.configure(text=f"樣本內（前 70%）\n訊號次數：{in_sample.signals}\n勝率：{in_sample.hit_rate_pct:.1f}%\n平均報酬：{in_sample.average_return_pct:+.2f}%")
        self.out_sample_label.configure(text=f"樣本外（後 30%，較具參考價值）\n訊號次數：{out_sample.signals}\n勝率：{out_sample.hit_rate_pct:.1f}%\n平均報酬：{out_sample.average_return_pct:+.2f}%")
