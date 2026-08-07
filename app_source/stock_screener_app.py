"""GUI screen for two independent, experimental market-wide screeners ("選股").

1. Golden-cross screen (stock_screener.py): a specific chart pattern (MA20
   crossing above MA60 + volume/slope/relative-strength/liquidity
   confirmations).
2. Momentum ranking (momentum_screener.py): purely "beat the rest of the
   liquid, tracked market over the last 60 trading days", no chart pattern
   required -- a different, independently-backtested dimension, added
   2026-08-07 after re-validating the effect on a larger, liquidity-filtered
   universe (see scripts/backtest_momentum_ranking.py).

Both deliberately labelled experimental: each rule was validated on a
partial, liquidity-skewed slice of the market (symbols with enough locally
archived history), not the full ~2000-symbol catalog, and only over one
historical sample. A candidate here means "matched a historically favorable
pattern", not "will go up" -- this screen places no orders and gives no
buy/sell instruction.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

import ui_theme
from stock_screener import ScreenerCandidate, scan_market
from momentum_screener import MomentumCandidate, scan_momentum_leaders
from storage_paths import storage_paths


class StockScreenerApp(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12)
        ttk.Label(
            self,
            text="選股為實驗性功能：規則（黃金交叉＋帶量確認＋60日均線向上＋相對強勢＋流動性門檻）"
                 "是依本機已有歷史資料的一部分股票回測而來，尚未涵蓋全市場，也只跑過一段歷史區間。"
                 "符合條件僅代表「過去統計上表現較好的型態」，不是「會上漲」的保證，本系統不執行任何下單委託。",
            foreground=ui_theme.WARNING, wraplength=900, justify="left",
        ).pack(anchor="w")

        controls = ttk.Frame(self); controls.pack(fill="x", pady=(10, 0))
        ttk.Button(controls, text="重新掃描", style="Primary.TButton", command=self.refresh).pack(side="left")
        self.status = ttk.Label(controls, foreground="#555555"); self.status.pack(side="left", padx=(12, 0))

        columns = ("symbol", "signal_date", "days_since", "relative_volume", "slope", "relative_strength", "liquidity", "price_at_signal", "current_price", "return_since")
        labels = ("股票代號", "訊號日期", "距今交易日", "量能倍數", "60日均線斜率%", "相對強勢%", "近20日均額(百萬)", "訊號當日價", "現價", "訊號後報酬%")
        self.table = ttk.Treeview(self, columns=columns, show="headings", height=16)
        for key, label in zip(columns, labels):
            self.table.heading(key, text=label); self.table.column(key, width=100, anchor="center")
        self.table.pack(fill="both", expand=True, pady=(10, 0))
        self.table.tag_configure("gain", foreground=ui_theme.GAIN)
        self.table.tag_configure("loss", foreground=ui_theme.LOSS)
        self.table.tag_configure("neutral", foreground=ui_theme.NEUTRAL)

        ttk.Separator(self).pack(fill="x", pady=(14, 10))
        ttk.Label(
            self,
            text="動量排名為另一個獨立維度：純粹依「近60個交易日報酬贏過同期全市場（已通過流動性門檻）多少」排名，"
                 "不要求黃金交叉型態。依 scripts/backtest_momentum_ranking.py 的回測（1,271檔、流動性門檻NT$20M/日），"
                 "前20%動量族群樣本外持有60日：47.23%正報酬、平均+7.24%（標準差36.97%），"
                 "對照後20%的46.14%、+2.01%——母體平均有優勢，但個股離散度很大，不是保證，本系統不執行任何下單委託。",
            foreground=ui_theme.WARNING, wraplength=900, justify="left",
        ).pack(anchor="w")

        momentum_controls = ttk.Frame(self); momentum_controls.pack(fill="x", pady=(10, 0))
        ttk.Button(momentum_controls, text="重新掃描動量排名", style="Primary.TButton", command=self.refresh_momentum).pack(side="left")
        self.momentum_status = ttk.Label(momentum_controls, foreground="#555555"); self.momentum_status.pack(side="left", padx=(12, 0))

        momentum_columns = ("symbol", "as_of_date", "trailing_return", "percentile", "liquidity", "current_price")
        momentum_labels = ("股票代號", "資料日期", "近60日報酬%", "動量百分位", "近20日均額(百萬)", "現價")
        self.momentum_table = ttk.Treeview(self, columns=momentum_columns, show="headings", height=12)
        for key, label in zip(momentum_columns, momentum_labels):
            self.momentum_table.heading(key, text=label); self.momentum_table.column(key, width=110, anchor="center")
        self.momentum_table.pack(fill="both", expand=True, pady=(10, 0))
        self.momentum_table.tag_configure("gain", foreground=ui_theme.GAIN)
        self.momentum_table.tag_configure("loss", foreground=ui_theme.LOSS)

        self._results: queue.Queue = queue.Queue()
        self._momentum_results: queue.Queue = queue.Queue()
        self.refresh()
        self.refresh_momentum()

    def refresh(self) -> None:
        # scan_market() reads every locally tracked symbol's full history --
        # already ~2s against the real production database and only grows as
        # more of the market gets backfilled. Running it synchronously here
        # (as the first version of this tab did) pushed StockAiApp
        # construction past the 1s budget test_window_construction_does_not_
        # block_on_startup_checks enforces -- the exact "slow work during
        # window construction" anti-pattern this app has already had to fix
        # more than once (verify_archive, run_startup_check).
        #
        # The background thread only ever touches self._results (a
        # thread-safe queue.Queue), never Tk/Tcl directly -- confirmed live
        # that calling self.after(...) FROM the background thread itself
        # (the pattern this file used at first) can silently never fire: Tcl
        # is not safe to call into from a thread other than the one running
        # its event loop. Polling from the main thread via self.after is the
        # side that's actually safe to call Tk/Tcl from.
        self.status.configure(text="掃描中…")
        threading.Thread(target=self._scan_worker, daemon=True).start()
        self.after(100, self._poll_results)

    def _scan_worker(self) -> None:
        try:
            candidates = scan_market(storage_paths()["history_database"])
            error: Exception | None = None
        except Exception as caught_error:
            candidates = []
            error = caught_error
        self._results.put((candidates, error))

    def _poll_results(self) -> None:
        try:
            candidates, error = self._results.get_nowait()
        except queue.Empty:
            try:
                self.after(100, self._poll_results)
            except RuntimeError:
                pass  # the window/tab was closed before the background scan finished
            return
        self._apply_results(candidates, error)

    def _apply_results(self, candidates: list[ScreenerCandidate], error: Exception | None) -> None:
        self.table.delete(*self.table.get_children())
        if error is not None:
            self.status.configure(text=f"掃描失敗：{error}")
            return
        for candidate in candidates:
            tag = "gain" if candidate.return_since_signal_pct > 0 else "loss" if candidate.return_since_signal_pct < 0 else "neutral"
            self.table.insert(
                "", "end", tags=(tag,),
                values=(
                    candidate.symbol,
                    candidate.signal_date.isoformat(),
                    candidate.days_since_signal,
                    f"{candidate.relative_volume:.2f}",
                    f"{candidate.ma_long_slope_pct:+.2f}",
                    f"{candidate.relative_strength_pct:+.2f}",
                    f"{candidate.avg_dollar_volume / 1_000_000:,.1f}",
                    f"{candidate.price_at_signal:.2f}",
                    f"{candidate.current_price:.2f}",
                    f"{candidate.return_since_signal_pct:+.2f}",
                ),
            )
        self.status.configure(text=f"符合條件：{len(candidates)} 檔（僅本機已有足夠歷史資料的股票）")

    def refresh_momentum(self) -> None:
        # Same background-thread + queue.Queue polling pattern as refresh()
        # above, for the same reason: scan_momentum_leaders() reads every
        # locally tracked symbol's full history and must not run
        # synchronously during window construction or a button click.
        self.momentum_status.configure(text="掃描中…")
        threading.Thread(target=self._momentum_scan_worker, daemon=True).start()
        self.after(100, self._poll_momentum_results)

    def _momentum_scan_worker(self) -> None:
        try:
            candidates = scan_momentum_leaders(storage_paths()["history_database"])
            error: Exception | None = None
        except Exception as caught_error:
            candidates = []
            error = caught_error
        self._momentum_results.put((candidates, error))

    def _poll_momentum_results(self) -> None:
        try:
            candidates, error = self._momentum_results.get_nowait()
        except queue.Empty:
            try:
                self.after(100, self._poll_momentum_results)
            except RuntimeError:
                pass  # the window/tab was closed before the background scan finished
            return
        self._apply_momentum_results(candidates, error)

    def _apply_momentum_results(self, candidates: list[MomentumCandidate], error: Exception | None) -> None:
        self.momentum_table.delete(*self.momentum_table.get_children())
        if error is not None:
            self.momentum_status.configure(text=f"掃描失敗：{error}")
            return
        for candidate in candidates:
            tag = "gain" if candidate.trailing_return_pct > 0 else "loss"
            self.momentum_table.insert(
                "", "end", tags=(tag,),
                values=(
                    candidate.symbol,
                    candidate.as_of_date.isoformat(),
                    f"{candidate.trailing_return_pct:+.2f}",
                    f"{candidate.percentile_rank:.1f}",
                    f"{candidate.avg_dollar_volume / 1_000_000:,.1f}",
                    f"{candidate.current_price:.2f}",
                ),
            )
        self.momentum_status.configure(text=f"前20%動量族群：{len(candidates)} 檔（僅本機已有足夠歷史資料且達流動性門檻的股票）")
