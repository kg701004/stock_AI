"""Single-window desktop integration of the tested Stock AI modules."""
from __future__ import annotations

import tkinter as tk
import threading
import os
import sys
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import ui_theme
from holdings_manager import HoldingsManager
from historical_coverage import check_coverage
from historical_storage import archive_and_import
from factor_score_app import FactorScoreApp
from factor_score_store import load_all_current_assessments
from portfolio import Position, load_positions_csv
from portfolio_allocation import build_allocation_plan, load_allocation_rules
from portfolio_preferences import get_cash_balance, set_cash_balance
from portfolio_risk import assess_owner_portfolio, load_risk_rules
from portfolio_risk_preferences import load as load_risk_preferences, save as save_risk_preferences
from portfolio_advanced_risk import assess as assess_advanced_risk
from security_catalog import list_all_symbols, load_security_metadata, resolve as resolve_symbol
from data_integrity import verify_data_integrity
from price_chart_data import WINDOW_CHOICES, load_recent_closes
from storage_paths import has_user_storage_config, storage_paths
from storage_setup_app import StorageSetupApp
from transaction_ledger import calculate_holdings
from update_manager import SCHEDULES, list_statuses, run_all_public_daily_updates, run_manual_update
from update_manager import run_startup_check
from broker_fee_sync import verify_and_cache
from visualization import sector_pie_chart, factor_heatmap, price_chart
from historical_backfill import estimate_work, plan_pending_months, run_backfill
from beta_hedge import CONTRACT_LABELS, CONTRACT_POINT_VALUES, suggest_hedge
from hedge_positions import load_position as load_hedge_position, save_position as save_hedge_position
from short_screening_app import ShortScreeningApp
from backtest_app import BacktestApp
from historical_storage import verify_archive
from watchlist_app import WatchlistApp
from watchlist_repository import list_items
from notification_center import check_watchlist_triggers, list_notifications, record_notification
from weighted_analysis import assess_stock, load_weight_config
from judgement_weights import load as load_judgement_weights, save as save_judgement_weights

FACTOR_LABELS = {"technical":"技術面","market_breadth":"市場廣度","sector_rotation":"產業輪動","fundamentals":"基本面","institutional_flow":"法人動向","derivatives":"衍生性商品","global_risk":"全球風險","sentiment":"情緒指標","events":"事件風險","liquidity":"流動性","valuation":"評價"}

class JudgementWeightFrame(ttk.Frame):
    """Editable 1..10 weights used by every factor-score assessment."""
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12); self.path=Path("config/judgement_weights.json"); self.values={}
        ttk.Label(self,text="判斷機制權重管理",font=("Microsoft JhengHei UI",12,"bold")).pack(anchor="w")
        ttk.Label(self,text="1 表示最低參考比重，10 表示最高；儲存後會立即影響自選、持股與配置使用的綜合分數。",foreground="#555555").pack(anchor="w",pady=(2,10))
        grid=ttk.Frame(self); grid.pack(anchor="w")
        for index,(factor,value) in enumerate(load_judgement_weights(self.path).items()):
            ttk.Label(grid,text=FACTOR_LABELS[factor],width=16).grid(row=index,column=0,sticky="w",pady=2)
            var=tk.IntVar(value=value); self.values[factor]=var
            ttk.Spinbox(grid,from_=1,to=10,textvariable=var,width=6).grid(row=index,column=1,sticky="w")
            ttk.Scale(grid,from_=1,to=10,orient="horizontal",variable=var,length=230).grid(row=index,column=2,padx=8)
        ttk.Button(self,text="儲存權重",style="Primary.TButton",command=self.save).pack(anchor="w",pady=(12,0)); self.status=ttk.Label(self,text=""); self.status.pack(anchor="w",pady=4)
    def save(self) -> None:
        try: save_judgement_weights(self.path,{name:int(var.get()) for name,var in self.values.items()}); self.status.configure(text="已儲存。下次重新分析時，所有評估將套用新權重。")
        except ValueError as error: messagebox.showerror("權重設定",str(error),parent=self.winfo_toplevel())


class PortfolioRiskFrame(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12)
        controls = ttk.Frame(self); controls.pack(fill="x")
        ttk.Label(controls, text="持有人").pack(side="left")
        self.owner = ttk.Combobox(controls, state="readonly", width=20); self.owner.pack(side="left", padx=(0, 8)); self.owner.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        ttk.Button(controls, text="更新風險", command=self.refresh).pack(side="left")
        self.summary = ttk.Label(self, font=("Microsoft JhengHei UI", 11, "bold")); self.summary.pack(anchor="w", pady=(10, 4))
        self.sample_data_warn = ttk.Label(self, style="Danger.TLabel", font=("Microsoft JhengHei UI", 10, "bold"))
        self.sample_data_warn.pack(anchor="w", pady=(0, 4))
        tables = ttk.Frame(self); tables.pack(fill="both", expand=True)
        self.holdings = ttk.Treeview(tables, columns=("symbol", "weight"), show="headings", height=8); self.sectors = ttk.Treeview(tables, columns=("sector", "weight"), show="headings", height=8)
        for tree, first, label in ((self.holdings, "symbol", "股票代號"), (self.sectors, "sector", "產業")):
            tree.heading(first, text=label); tree.heading("weight", text="權重"); tree.column(first, width=180, anchor="center"); tree.column("weight", width=120, anchor="center")
            ui_theme.stripe(tree)
        self.chart_area = ttk.Frame(tables, width=260)
        self.holdings.grid(row=0, column=0, padx=(0, 8), sticky="nsew"); self.sectors.grid(row=0, column=1, padx=(0, 8), sticky="nsew"); self.chart_area.grid(row=0, column=2, sticky="nsew")
        tables.columnconfigure(0, weight=1); tables.columnconfigure(1, weight=1); tables.columnconfigure(2, weight=1)
        ttk.Label(self, text="風險警示", style="Heading.TLabel").pack(anchor="w", pady=(10, 2))
        self.warnings = tk.Text(self, height=9, wrap="word", state="disabled", background=ui_theme.SURFACE, relief="flat", borderwidth=1, highlightbackground=ui_theme.BORDER, highlightthickness=1)
        self.warnings.pack(fill="x")
        self.warnings.tag_configure("warn", foreground=ui_theme.WARNING)
        self.warnings.tag_configure("info", foreground=ui_theme.MUTED)
        self.refresh()

    def refresh(self) -> None:
        ledger = calculate_holdings(storage_paths()["decision_database"])
        positions = [Position(x.owner, x.symbol, x.shares, x.average_cost, x.current_price, datetime.now().astimezone()) for x in ledger if x.current_price is not None]
        if not positions:
            positions = load_positions_csv(Path("data/sample_positions.csv"))
            self._using_sample_data = True
            self.sample_data_warn.configure(text="⚠ 目前顯示的是示範資料（非您的真實持股），請先在「持股管理」新增交易並設定現價")
        else:
            self._using_sample_data = False
            self.sample_data_warn.configure(text="")
        owners = sorted({x.owner for x in positions}); self.owner["values"] = owners
        if owners and self.owner.get() not in owners: self.owner.set(owners[0])
        if not owners: return
        metadata = load_security_metadata(storage_paths()["history_database"], symbols=[p.symbol for p in positions]); settings = load_risk_preferences(storage_paths()["decision_database"], self.owner.get()); report = assess_owner_portfolio(self.owner.get(), positions, metadata, settings)
        advanced = assess_advanced_risk(storage_paths()["history_database"], [p for p in positions if p.owner == self.owner.get()], metadata, settings)
        self.summary.configure(text=f"組合市值：{report.total_market_value:,.0f}　組合 Beta：{report.portfolio_beta:.2f}")
        for tree in (self.holdings, self.sectors): tree.delete(*tree.get_children())
        for index, (symbol, weight) in enumerate(report.holding_weights_pct.items()): self.holdings.insert("", "end", tags=(ui_theme.stripe_tag(index),), values=(symbol, f"{weight:.2f}%"))
        for index, (sector, weight) in enumerate(report.sector_weights_pct.items()): self.sectors.insert("", "end", tags=(ui_theme.stripe_tag(index),), values=(sector, f"{weight:.2f}%"))
        for child in self.chart_area.winfo_children(): child.destroy()
        if report.sector_weights_pct: sector_pie_chart(self.chart_area, report.sector_weights_pct)
        # Genuine breaches (report.warnings/advanced.warnings) are visually distinct ("warn",
        # amber) from plain informational readouts ("info", muted) -- previously every line
        # (mode/window/beta/vol/VaR/stress numbers and actual concentration/correlation
        # breaches) was dumped into one undifferentiated bullet list.
        info_lines = [f"風險模式：{settings['profile']}；窗口：{advanced.window_days} 日", f"動態 Beta：{'資料不足' if advanced.dynamic_beta is None else advanced.dynamic_beta}", f"年化波動：{'資料不足' if advanced.annualized_volatility_pct is None else str(advanced.annualized_volatility_pct)+'%'}", f"歷史 VaR：{'未啟用或資料不足' if advanced.var_pct is None else str(advanced.var_pct)+'%'}"] + [f"壓力測試 {name}：{loss}%" for name, loss in advanced.stress_losses]
        warn_lines = list(report.warnings) + list(advanced.warnings)
        self.warnings.configure(state="normal"); self.warnings.delete("1.0", "end")
        if warn_lines:
            for line in warn_lines: self.warnings.insert("end", f"⚠ {line}\n", ("warn",))
        else:
            self.warnings.insert("end", "目前未觸發集中度、Beta 或資料缺漏警示。\n", ("info",))
        for line in info_lines: self.warnings.insert("end", f"· {line}\n", ("info",))
        self.warnings.configure(state="disabled")


class DataManagementFrame(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12); self.paths = storage_paths(); self.history_database = self.paths["history_database"]
        ttk.Label(self, text=f"歷史資料庫：{self.history_database}\n決策資料庫：{self.paths['decision_database']}\n原始封存：{self.paths['raw_archive']}\n備份：{self.paths['backups']}", justify="left").pack(anchor="nw")
        ttk.Label(self, text="資料更新狀態", font=("Microsoft JhengHei UI", 11, "bold")).pack(anchor="w", pady=(12, 4))
        coverage = ttk.Frame(self); coverage.pack(anchor="w", pady=(10, 2))
        ttk.Label(coverage, text="十年回測資料檢核（股票代號）").pack(side="left")
        self.coverage_symbol = ttk.Entry(coverage, width=10); self.coverage_symbol.pack(side="left", padx=5)
        ttk.Button(coverage, text="檢查完整度", command=self.check_history_coverage).pack(side="left")
        ttk.Button(coverage, text="匯入歷史 CSV", command=self.import_history_csv).pack(side="left", padx=(6, 0))
        self.coverage_status = ttk.Label(self, foreground="#555555", wraplength=900, justify="left")
        self.coverage_status.pack(anchor="w", pady=(0, 8))
        self.table = ttk.Treeview(self, columns=("source", "scheduled", "latest", "status", "detail"), show="headings", height=7)
        for key, text, width in (("source", "來源", 140), ("scheduled", "預計更新時間", 220), ("latest", "最新更新", 200), ("status", "狀態", 100), ("detail", "明細", 360)):
            self.table.heading(key, text=text); self.table.column(key, width=width, anchor="center")
        self.table.tag_configure("success", foreground=ui_theme.SUCCESS)
        self.table.tag_configure("error", foreground=ui_theme.ERROR)
        self.table.tag_configure("warning", foreground=ui_theme.WARNING)
        self.table.tag_configure("muted", foreground=ui_theme.MUTED)
        self.table.pack(fill="x"); buttons = ttk.Frame(self); buttons.pack(anchor="w", pady=(8, 0)); self.all_update_button=ttk.Button(buttons, text="更新全部上市／上櫃並驗證", style="Primary.TButton", command=self.update_all_daily); self.all_update_button.pack(side="left", padx=(0, 6)); ttk.Button(buttons, text="手動更新選取來源", command=self.manual_update).pack(side="left", padx=(0, 6)); ttk.Button(buttons, text="重新整理", command=self.refresh).pack(side="left")
        self.progress=ttk.Progressbar(self,orient="horizontal",length=520,mode="determinate",maximum=4); self.progress.pack(anchor="w",pady=(8,0)); self.progress_status=ttk.Label(self,text="尚未開始更新"); self.progress_status.pack(anchor="w")
        ttk.Label(self, text="TWSE、TPEx 使用公開日終資料；夜盤與 VIX 尚未接入，系統會如實標記。", foreground="#555555").pack(anchor="w", pady=(8, 0))

        ttk.Label(self, text="歷史資料批次回補（支援 TWSE 上市／TPEx 上櫃）", font=("Microsoft JhengHei UI", 11, "bold")).pack(anchor="w", pady=(14, 4))
        scope_row = ttk.Frame(self); scope_row.pack(anchor="w")
        ttk.Label(scope_row, text="股票代號（逗號分隔；留空則用目前持股＋自選股）").pack(side="left")
        self.backfill_symbols = ttk.Entry(scope_row, width=32); self.backfill_symbols.pack(side="left", padx=6)
        ttk.Label(scope_row, text="回補年數（TWSE 最早可回補至 2010 年、TPEx 最早可回補至 1994 年，超過的年數會自動略過）").pack(side="left", padx=(12, 0))
        self.backfill_years = ttk.Spinbox(scope_row, from_=1, to=40, width=4); self.backfill_years.pack(side="left", padx=6); self.backfill_years.set(40)
        backfill_buttons = ttk.Frame(self); backfill_buttons.pack(anchor="w", pady=(6, 0))
        ttk.Button(backfill_buttons, text="回補持股＋自選股全部", command=self.estimate_backfill_all_tracked).pack(side="left", padx=(0, 6))
        ttk.Button(backfill_buttons, text="全歷史資料下載（全部個股）", command=self.estimate_backfill_all_symbols).pack(side="left", padx=(0, 6))
        ttk.Button(backfill_buttons, text="估算工作量", command=self.estimate_backfill).pack(side="left", padx=(0, 6))
        self.start_backfill_button = ttk.Button(backfill_buttons, text="開始回補", style="Primary.TButton", command=self.start_backfill, state="disabled"); self.start_backfill_button.pack(side="left", padx=(0, 6))
        self.stop_backfill_button = ttk.Button(backfill_buttons, text="停止", style="Danger.TButton", command=self.stop_backfill, state="disabled"); self.stop_backfill_button.pack(side="left")
        self.backfill_status = ttk.Label(self, foreground="#555555", wraplength=900, justify="left"); self.backfill_status.pack(anchor="w", pady=(4, 0))
        self.backfill_progress = ttk.Progressbar(self, orient="horizontal", length=520, mode="determinate"); self.backfill_progress.pack(anchor="w", pady=(4, 0))
        self._pending_backfill: list | None = None
        self._stop_backfill = False

        ttk.Label(self, text="資料完整性驗證", font=("Microsoft JhengHei UI", 11, "bold")).pack(anchor="w", pady=(14, 4))
        integrity_row = ttk.Frame(self); integrity_row.pack(anchor="w")
        ttk.Label(integrity_row, text="股票代號（逗號分隔；留空則檢查全部本機已回補資料）").pack(side="left")
        self.integrity_symbols = ttk.Entry(integrity_row, width=32); self.integrity_symbols.pack(side="left", padx=6)
        ttk.Button(integrity_row, text="驗證資料完整性", command=self.run_integrity_check).pack(side="left", padx=(6, 0))
        self.integrity_status = ttk.Label(self, foreground="#555555", wraplength=900, justify="left"); self.integrity_status.pack(anchor="w", pady=(4, 0))

        self.refresh()

    STATUS_TAGS = {"成功": "success", "失敗": "error", "尚未接入": "warning", "尚未更新": "muted"}

    def refresh(self) -> None:
        self.table.delete(*self.table.get_children())
        for item in list_statuses(self.history_database):
            latest = "尚未更新" if item.last_updated_at is None else item.last_updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
            self.table.insert("", "end", tags=(self.STATUS_TAGS.get(item.status, "muted"),), values=(item.source, item.scheduled_time, latest, item.status, item.detail))

    def manual_update(self) -> None:
        selected = self.table.selection(); source = self.table.item(selected[0])["values"][0] if selected else next(item for item in SCHEDULES if item.startswith("TWSE"))
        result = run_manual_update(source, self.history_database, self.paths["imports"], self.paths["raw_archive"])
        failed = "成功" not in result
        record_notification(storage_paths()["decision_database"], "data_update", str(source), f"{source}：{result}", datetime.now().astimezone(), notify_os=failed)
        self.refresh()

    def update_all_daily(self) -> None:
        self.all_update_button.configure(state="disabled"); self.progress.configure(value=0); self.progress_status.configure(text="準備更新全市場日線…")
        threading.Thread(target=self._update_all_worker,daemon=True).start()

    def _set_progress(self, step: int, text: str) -> None:
        self.after(0, lambda: (self.progress.configure(value=step), self.progress_status.configure(text=text)))

    def _update_all_worker(self) -> None:
        results=[]
        try:
            self._set_progress(0,"正在更新 TWSE 上市全市場日線…")
            results.append(run_manual_update(next(item for item in SCHEDULES if item.startswith("TWSE")),self.history_database,self.paths["imports"],self.paths["raw_archive"]))
            self._set_progress(1,"正在更新 TPEx 上櫃全市場日線…")
            results.append(run_manual_update(next(item for item in SCHEDULES if item.startswith("TPEx")),self.history_database,self.paths["imports"],self.paths["raw_archive"]))
            self._set_progress(2,"正在更新 VIX 全球風險資料…")
            results.append(run_manual_update(next(item for item in SCHEDULES if item.startswith("VIX")),self.history_database,self.paths["imports"],self.paths["raw_archive"]))
            self._set_progress(3,"正在驗證壓縮封存與 SQLite 匯入…")
            errors=verify_archive(self.history_database); results.append("封存驗證通過" if not errors else "封存驗證失敗："+"、".join(errors))
            self._set_progress(4,"更新完成" if not errors else "更新完成，但封存驗證失敗")
        except Exception as error:
            results.append(f"更新失敗：{error}"); self._set_progress(4,"更新失敗")
        finally:
            summary = "；".join(results); failed = any("失敗" in item for item in results)
            try: record_notification(storage_paths()["decision_database"], "data_update", "ALL", f"全部日線更新：{summary}", datetime.now().astimezone(), notify_os=failed)
            except Exception: pass
            self.after(0, lambda: (self.refresh(),self.all_update_button.configure(state="normal"),messagebox.showinfo("全部日線更新",summary,parent=self.winfo_toplevel())))

    def begin_startup_check(self) -> None:
        """Run the broker-fee verification and daily-data startup check off
        the main thread. Disables the same "更新全部上市／上櫃並驗證" button
        that a manual run disables -- since both can end up calling
        run_all_public_daily_updates against the same tables, sharing this
        one button as the mutual-exclusion guard means a manual click during
        the startup check is simply not possible (a disabled button doesn't
        fire), rather than two fetches racing each other."""
        self.all_update_button.configure(state="disabled")
        self.progress_status.configure(text="開機自動檢查中（費率／資料更新，於背景執行，不影響其他操作）…")
        threading.Thread(target=self._startup_check_worker, daemon=True).start()

    def _startup_check_worker(self) -> None:
        fee_result = verify_and_cache(self.paths["root"] / "kgi_fee_reference.json")
        check_result = run_startup_check(self.history_database, self.paths["imports"], self.paths["raw_archive"], decision_database=self.paths["decision_database"])
        integrity_result = self._tracked_symbols_integrity_summary()
        def done() -> None:
            self.all_update_button.configure(state="normal")
            self.progress_status.configure(text=f"開機自動檢查完成：{check_result}｜{fee_result}｜{integrity_result}")
            self.refresh()
        self.after(0, done)

    def _tracked_symbols_integrity_summary(self) -> str:
        """Runs the same three real checks as the "驗證資料完整性" button,
        scoped to holdings+watchlist so it stays fast on every startup,
        synchronized with the gap catch-up above rather than a separate
        manual step the user has to remember to run."""
        try:
            holdings_symbols = {item.symbol for item in calculate_holdings(self.paths["decision_database"])}
            watchlist_symbols = {item.symbol for item in list_items(self.paths["decision_database"])}
            symbols = sorted(holdings_symbols | watchlist_symbols)
            if not symbols:
                return "資料完整性：無持股或自選股，略過。"
            report = verify_data_integrity(self.history_database, symbols)
            if report.clean:
                return f"資料完整性：正常（已檢查 {report.total_bars_checked:,} 筆）。"
            problems = []
            if report.archive_errors:
                problems.append(f"封存錯誤 {len(report.archive_errors)} 筆")
            if report.ohlc_violations:
                problems.append(f"OHLC 異常 {len(report.ohlc_violations)} 筆")
            if report.symbols_with_gaps:
                problems.append(f"交易日缺口 {len(report.symbols_with_gaps)} 檔")
            return "資料完整性：發現" + "、".join(problems) + "，請至「資料管理」頁進一步確認。"
        except Exception as error:
            return f"資料完整性：檢查失敗（{error}）。"

    def check_history_coverage(self) -> None:
        try:
            report = check_coverage(self.history_database, self.coverage_symbol.get(), years=10)
            span = "無" if report.first_date is None else f"{report.first_date} 至 {report.last_date}"
            years = "；".join(f"{year}:{count}" for year, count in report.yearly_bars)
            state = "可進行回測" if report.ready_for_backtest else "尚不可進行回測"
            self.coverage_status.configure(text=f"{state}｜資料區間：{span}｜總筆數：{report.total_bars}｜年度筆數：{years}\n{report.message}")
        except ValueError as error:
            messagebox.showerror("歷史資料檢核", str(error), parent=self.winfo_toplevel())

    def import_history_csv(self) -> None:
        filename = filedialog.askopenfilename(parent=self.winfo_toplevel(), title="選擇標準化歷史日線 CSV", filetypes=[("CSV", "*.csv")])
        if not filename:
            return
        try:
            checksum, inserted = archive_and_import(Path(filename), self.history_database, self.paths["raw_archive"])
            self.coverage_status.configure(text=f"匯入完成：{inserted} 筆；封存校驗碼：{checksum[:12]}。請輸入股票代號並檢查十年完整度。")
        except (OSError, ValueError) as error:
            messagebox.showerror("匯入歷史 CSV", str(error), parent=self.winfo_toplevel())

    def _backfill_symbols_list(self) -> list[str]:
        text = self.backfill_symbols.get().strip()
        if text:
            return [part.strip() for part in text.split(",") if part.strip()]
        holdings_symbols = {item.symbol for item in calculate_holdings(self.paths["decision_database"])}
        watchlist_symbols = {item.symbol for item in list_items(self.paths["decision_database"])}
        return sorted(holdings_symbols | watchlist_symbols)

    def estimate_backfill_all_tracked(self) -> None:
        """One-click "回補持股＋自選股全部" -- clears any manually-typed
        代號 so _backfill_symbols_list() falls back to its documented
        blank-field behavior (目前持股＋自選股 union), then estimates exactly
        like the 估算工作量 button. Deliberately still requires a manual
        「開始回補」 click afterwards -- this shortcut skips retyping symbols,
        not the "review the real request count/time first" safety step,
        since backfilling many symbols for many years is a real, sustained
        load on a free public API."""
        self.backfill_symbols.delete(0, "end")
        self.estimate_backfill()

    def estimate_backfill_all_symbols(self) -> None:
        """"全歷史資料下載" -- scope every symbol ever seen in a local daily
        snapshot (securities catalog), not just holdings/watchlist. This can
        be a genuinely large request count (thousands of symbols x years of
        months), so it still goes through the same 估算工作量 -> 開始回補 flow
        as every other backfill: the real request count and time estimate
        must be shown and confirmed before anything is actually fetched."""
        symbols = list_all_symbols(self.history_database)
        if not symbols:
            messagebox.showinfo("全歷史資料下載", "本機股票名錄尚未建立，請先執行一次「更新全部上市／上櫃並驗證」。", parent=self.winfo_toplevel()); return
        self.backfill_symbols.delete(0, "end")
        self.backfill_symbols.insert(0, ",".join(symbols))
        self.estimate_backfill()

    def estimate_backfill(self) -> None:
        symbols = self._backfill_symbols_list()
        if not symbols:
            messagebox.showinfo("估算工作量", "沒有可回補的股票代號：請輸入代號，或先在持股／自選股加入股票。", parent=self.winfo_toplevel()); return
        pending = plan_pending_months(self.history_database, symbols, years=int(self.backfill_years.get()))
        count, seconds = estimate_work(pending)
        self._pending_backfill = pending
        # A raw "還剩 N 次請求" barely visibly moves for a large symbol list
        # (e.g. 全歷史資料下載's ~2000 symbols) even after real progress, since
        # any one session only ever closes a small fraction of the whole --
        # this per-symbol count is what actually shows that progress persists.
        symbols_still_pending = {symbol for symbol, _year, _month in pending}
        completed_symbols = len(symbols) - len(symbols_still_pending)
        if not pending:
            self.backfill_status.configure(text=f"{len(symbols)} 檔股票在所選年數內都已回補完成，無需再次執行。")
            self.start_backfill_button.configure(state="disabled"); return
        self.backfill_status.configure(text=f"已完成 {completed_symbols}/{len(symbols)} 檔股票；剩餘 {len(symbols_still_pending)} 檔、共 {count} 次請求，約需 {seconds/60:.1f} 分鐘。確認後請按「開始回補」。")
        self.start_backfill_button.configure(state="normal")

    def start_backfill(self) -> None:
        if not self._pending_backfill:
            return
        symbols = self._backfill_symbols_list()
        self._stop_backfill = False
        self.start_backfill_button.configure(state="disabled"); self.stop_backfill_button.configure(state="normal")
        self.backfill_progress.configure(value=0, maximum=len(self._pending_backfill))
        threading.Thread(target=self._backfill_worker, args=(symbols, int(self.backfill_years.get())), daemon=True).start()

    def stop_backfill(self) -> None:
        self._stop_backfill = True
        self.backfill_status.configure(text="已收到停止指令，將在目前這筆完成後停止…")

    def _backfill_worker(self, symbols: list[str], years: int) -> None:
        # A raw "回補中 1426/544632" request counter barely visibly moves for
        # a large symbol list -- this tracks, cheaply and incrementally (no
        # per-callback database query), how many DISTINCT symbols have had
        # every one of their queued months processed, so the live status line
        # shows the same kind of per-symbol progress as the pre-run estimate
        # and the post-run summary, not just the huge, slow-moving request count.
        # "Processed" (attempted), not "succeeded" -- the callback doesn't
        # distinguish the two -- so this can be briefly optimistic if a
        # symbol's last month happens to fail; the definitive count is
        # recomputed for real from plan_pending_months once the run ends.
        remaining_months_by_symbol: dict[str, int] = {}
        for symbol, _year, _month in self._pending_backfill or []:
            remaining_months_by_symbol[symbol] = remaining_months_by_symbol.get(symbol, 0) + 1
        total_symbols = len(remaining_months_by_symbol)
        completed_symbols_so_far = 0

        def on_progress(index: int, total: int, label: str) -> None:
            nonlocal completed_symbols_so_far
            symbol = label.split(" ", 1)[0]
            if symbol in remaining_months_by_symbol:
                remaining_months_by_symbol[symbol] -= 1
                if remaining_months_by_symbol[symbol] <= 0:
                    completed_symbols_so_far += 1
            self.after(0, lambda: (
                self.backfill_progress.configure(value=index, maximum=total),
                self.backfill_status.configure(text=f"回補中 {index}/{total}（已完成 {completed_symbols_so_far}/{total_symbols} 檔股票）：{label}"),
            ))
        summary = run_backfill(
            self.history_database, self.paths["imports"], self.paths["raw_archive"], symbols, years=years,
            progress_callback=on_progress, should_stop=lambda: self._stop_backfill,
        )
        remaining = plan_pending_months(self.history_database, symbols, years=years)
        remaining_symbols = {symbol for symbol, _year, _month in remaining}
        completed_symbols = len(symbols) - len(remaining_symbols)
        def done() -> None:
            self.start_backfill_button.configure(state="disabled"); self.stop_backfill_button.configure(state="disabled")
            message = (
                f"{'已中止' if summary.stopped_early else '完成'}：嘗試 {summary.attempted} 筆、成功 {summary.succeeded} 筆"
                + (f"、失敗 {len(summary.failed)} 筆" if summary.failed else "")
                + f"｜累計已完成 {completed_symbols}/{len(symbols)} 檔股票"
            )
            self.backfill_status.configure(text=message)
            self._pending_backfill = None
            messagebox.showinfo("歷史資料回補", message + ("\n\n失敗明細：\n" + "\n".join(summary.failed) if summary.failed else ""), parent=self.winfo_toplevel())
        self.after(0, done)

    def run_integrity_check(self) -> None:
        text = self.integrity_symbols.get().strip()
        symbols = [part.strip() for part in text.split(",") if part.strip()] or None
        self.integrity_status.configure(text="驗證中，請稍候…（掃描全部本機資料時可能需要數秒到數十秒）")
        threading.Thread(target=self._integrity_worker, args=(symbols,), daemon=True).start()

    def _integrity_worker(self, symbols: list[str] | None) -> None:
        report = verify_data_integrity(self.history_database, symbols)

        def show() -> None:
            if report.total_bars_checked == 0:
                self.integrity_status.configure(text="本機尚無可驗證的日線資料。")
                return
            lines = [f"已檢查 {report.total_bars_checked:,} 筆日線資料。"]
            lines.append("封存完整：無錯誤" if not report.archive_errors else f"封存錯誤：{len(report.archive_errors)} 筆（原始壓縮檔遺失或校驗碼不符，需重新匯入）")
            lines.append("OHLC 合理性：無異常" if not report.ohlc_violations else f"OHLC 異常：{len(report.ohlc_violations)} 筆（高低價與開收價不合理，建議重新回補該股票）")
            lines.append("交易日缺口：未發現" if not report.symbols_with_gaps else "交易日缺口：" + "、".join(f"{symbol}（缺 {count} 天）" for symbol, count in report.symbols_with_gaps[:10]) + ("…" if len(report.symbols_with_gaps) > 10 else ""))
            if not report.clean:
                lines.append("（交易日缺口可能是真實停牌，不一定是資料錯誤，建議人工確認後再決定是否重新回補。）")
            self.integrity_status.configure(text="\n".join(lines))
        self.after(0, show)


class RiskSettingsFrame(ttk.Frame):
    """Per-owner switches for advanced portfolio risk calculations."""
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12); self.database=storage_paths()["decision_database"]
        top=ttk.Frame(self); top.pack(anchor="w")
        ttk.Label(top,text="持有人").pack(side="left"); self.owner=ttk.Combobox(top,width=16,state="readonly"); self.owner.pack(side="left",padx=5); self.owner.bind("<<ComboboxSelected>>",lambda _:self.refresh())
        ttk.Label(top,text="風險模式").pack(side="left"); self.profile=ttk.Combobox(top,values=("保守","平衡","積極","自訂"),width=10,state="readonly"); self.profile.pack(side="left",padx=5)
        ttk.Label(top,text="計算窗口").pack(side="left"); self.window=ttk.Combobox(top,values=("60","120","250"),width=6,state="readonly"); self.window.pack(side="left",padx=5)
        self.options={name:tk.BooleanVar() for name in ("enable_correlation","enable_dynamic_beta","enable_var_es","enable_stress_test","enable_rebalance","enable_correlation_stress")}
        labels={"enable_correlation":"相關性","enable_dynamic_beta":"動態 Beta","enable_var_es":"VaR／ES","enable_stress_test":"壓力測試","enable_rebalance":"再平衡建議","enable_correlation_stress":"相關性壓力情境（假設危機時相關性收斂至1，非歷史實測）"}
        for name,var in self.options.items(): ttk.Checkbutton(self,text=labels[name],variable=var).pack(anchor="w",pady=2)
        ttk.Button(self,text="儲存設定",style="Primary.TButton",command=self.save).pack(anchor="w",pady=10); self.status=ttk.Label(self,foreground="#555555"); self.status.pack(anchor="w")
        self.refresh_owners()
    def refresh_owners(self):
        owners=sorted({x.owner for x in calculate_holdings(self.database)})
        self.owner["values"]=owners
        if owners: self.owner.set(owners[0]); self.refresh()
        else: self.status.configure(text="請先在持股管理建立持有人與持股。")
    def refresh(self):
        if not self.owner.get(): return
        value=load_risk_preferences(self.database,self.owner.get()); self.profile.set(str(value["profile"])); self.window.set(str(value["window_days"]))
        for name,var in self.options.items(): var.set(bool(value[name]))
    def save(self):
        if not self.owner.get(): return
        value=save_risk_preferences(self.database,self.owner.get(),self.profile.get(),{"window_days":int(self.window.get()),**{name:var.get() for name,var in self.options.items()}})
        self.status.configure(text=f"已儲存 {self.owner.get()} 的 {value['profile']} 風險設定。")

class AllocationFrame(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12); controls = ttk.Frame(self); controls.pack(fill="x")
        ttk.Label(controls, text="持有人").pack(side="left"); self.owner = ttk.Combobox(controls, state="readonly", width=20); self.owner.pack(side="left", padx=(0, 8)); self.owner.bind("<<ComboboxSelected>>", self.owner_selected)
        ttk.Label(controls, text="現金餘額").pack(side="left", padx=(12, 0))
        self.cash_balance = tk.StringVar(value="0")
        ttk.Entry(controls, textvariable=self.cash_balance, width=14).pack(side="left", padx=(4, 4))
        ttk.Button(controls, text="儲存現金餘額", style="Primary.TButton", command=self.save_cash).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="更新配置建議", command=self.refresh).pack(side="left", padx=(8, 0))
        self.summary = ttk.Label(self, font=("Microsoft JhengHei UI", 11, "bold")); self.summary.pack(anchor="w", pady=(10, 4))
        columns = ("symbol", "sector", "score", "current", "target", "adjustment", "action"); self.table = ttk.Treeview(self, columns=columns, show="headings", height=10)
        for key, label in zip(columns, ("股票代號", "產業", "綜合分數", "目前權重", "目標權重", "建議調整金額", "建議")): self.table.heading(key, text=label); self.table.column(key, width=125, anchor="center")
        self.table.tag_configure("gain", foreground=ui_theme.GAIN)
        self.table.tag_configure("loss", foreground=ui_theme.LOSS)
        self.table.tag_configure("neutral", foreground=ui_theme.NEUTRAL)
        self.table.pack(fill="both", expand=True); self.warnings = tk.Text(self, height=6, wrap="word", state="disabled"); self.warnings.pack(fill="x", pady=(8, 0)); self.refresh()

    def owner_selected(self, _event: object) -> None:
        if self.owner.get():
            self.cash_balance.set(f"{get_cash_balance(storage_paths()['decision_database'], self.owner.get()):g}")
        self.refresh()

    def save_cash(self) -> None:
        if not self.owner.get():
            return
        try:
            set_cash_balance(storage_paths()["decision_database"], self.owner.get(), float(self.cash_balance.get()))
        except ValueError as error:
            messagebox.showerror("無法儲存現金餘額", str(error), parent=self.winfo_toplevel()); return
        self.refresh()

    def refresh(self) -> None:
        database = storage_paths()["decision_database"]; ledger = calculate_holdings(database); owners = sorted({item.owner for item in ledger if item.market_value is not None}); self.owner["values"] = owners
        if owners and self.owner.get() not in owners: self.owner.set(owners[0])
        self.table.delete(*self.table.get_children())
        if not owners: self.summary.configure(text="請先新增交易並輸入現價，才能建立個人配置建議。"); return
        cash_balance = get_cash_balance(database, self.owner.get())
        self.cash_balance.set(f"{cash_balance:g}")
        weight_config = load_weight_config(Path("config/analysis_weights.json"))
        scores = {symbol: assess_stock(row, weight_config).final_score for symbol, row in load_all_current_assessments(database, storage_paths()["history_database"]).items()}
        watchlist_symbols = [item.symbol for item in list_items(database)]
        plan = build_allocation_plan(self.owner.get(), ledger, scores, load_security_metadata(storage_paths()["history_database"], symbols={item.symbol for item in ledger} | set(watchlist_symbols)), load_allocation_rules(Path("config/allocation_rules.json")), watchlist_symbols, cash_balance=cash_balance)
        self.summary.configure(text=f"總資產（含現金）：{plan.portfolio_value:,.0f}　現金：{plan.cash_balance:,.0f}（{plan.cash_weight_pct:.2f}%）")
        action_tags = {"建立部位": "gain", "加碼": "gain", "減碼": "loss", "維持": "neutral"}
        for item in plan.suggestions: self.table.insert("", "end", tags=(action_tags.get(item.action, "neutral"),), values=(item.symbol, item.sector, f"{item.score:.1f}", f"{item.current_weight_pct:.2f}%", f"{item.target_weight_pct:.2f}%", f"{item.adjustment_value:,.0f}", item.action))
        self.warnings.configure(state="normal"); self.warnings.delete("1.0", "end"); self.warnings.insert("1.0", "\n".join(f"- {x}" for x in plan.warnings) or "配置依已登錄持股、現金餘額、個股與產業上限計算。" ); self.warnings.configure(state="disabled")


class HedgeAdviceFrame(ttk.Frame):
    """Suggest an index-futures contract count to move the portfolio to a target Beta. Advisory only -- never places an order."""
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12)
        controls = ttk.Frame(self); controls.pack(fill="x")
        ttk.Label(controls, text="持有人").pack(side="left")
        self.owner = ttk.Combobox(controls, state="readonly", width=18); self.owner.pack(side="left", padx=(0, 8)); self.owner.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        ttk.Button(controls, text="更新", command=self.refresh).pack(side="left")
        self.summary = ttk.Label(self, font=("Microsoft JhengHei UI", 11, "bold")); self.summary.pack(anchor="w", pady=(10, 8))
        self.sample_data_warn = ttk.Label(self, style="Danger.TLabel", font=("Microsoft JhengHei UI", 10, "bold"))
        self.sample_data_warn.pack(anchor="w", pady=(0, 8))

        target_row = ttk.Frame(self); target_row.pack(anchor="w", fill="x")
        ttk.Label(target_row, text="目標 Beta", width=12).pack(side="left")
        self.target_beta = tk.DoubleVar(value=0.0)
        ttk.Scale(target_row, from_=0, to=1.5, orient="horizontal", variable=self.target_beta, length=260, command=lambda _: self.recalculate()).pack(side="left", padx=8)
        self.target_label = ttk.Label(target_row, text="0.00", width=6); self.target_label.pack(side="left")
        presets = ttk.Frame(self); presets.pack(anchor="w", pady=(4, 10))
        for label, value in (("0（完全避險）", 0.0), ("0.5（部分避險）", 0.5), ("1.0（不避險）", 1.0)):
            ttk.Button(presets, text=label, command=lambda v=value: self._set_target(v)).pack(side="left", padx=(0, 6))

        contract_row = ttk.Frame(self); contract_row.pack(anchor="w")
        ttk.Label(contract_row, text="期貨規模", width=12).pack(side="left")
        self.contract = tk.StringVar(value="MTX")
        for code in ("MTX", "TX", "TMF"):
            ttk.Radiobutton(contract_row, text=f"{CONTRACT_LABELS[code]}（{CONTRACT_POINT_VALUES[code]:.0f} 元/點）", value=code, variable=self.contract, command=self._contract_changed).pack(side="left", padx=(0, 10))

        index_row = ttk.Frame(self); index_row.pack(anchor="w", pady=(10, 0))
        ttk.Label(index_row, text="台指期現在點數", width=12).pack(side="left")
        self.index_points = tk.StringVar(value="21600")
        entry = ttk.Entry(index_row, textvariable=self.index_points, width=10); entry.pack(side="left", padx=8)
        entry.bind("<KeyRelease>", lambda _: self.recalculate())
        ttk.Label(index_row, text="（需自行查詢輸入，系統無即時期貨報價）", foreground="#555555").pack(side="left")

        held_row = ttk.Frame(self); held_row.pack(anchor="w", pady=(10, 0))
        ttk.Label(held_row, text="目前已持有口數", width=12).pack(side="left")
        self.held_contracts = tk.StringVar(value="0")
        held_entry = ttk.Entry(held_row, textvariable=self.held_contracts, width=10); held_entry.pack(side="left", padx=8)
        held_entry.bind("<KeyRelease>", lambda _: self.recalculate())
        ttk.Button(held_row, text="儲存目前部位", style="Primary.TButton", command=self.save_held_position).pack(side="left", padx=(8, 0))
        ttk.Label(held_row, text="（正值＝做多，負值＝放空；用於扣除已避險部位，避免重複建議）", foreground="#555555").pack(side="left", padx=(8, 0))

        self.result = ttk.Label(self, font=("Microsoft JhengHei UI", 13, "bold")); self.result.pack(anchor="w", pady=(14, 2))
        self.formula = ttk.Label(self, foreground="#555555"); self.formula.pack(anchor="w")
        ttk.Label(self, text="此為口數建議，不會自動下單；期貨保證金、每日結算損益與到期轉倉需自行在期貨帳戶執行。", foreground=ui_theme.WARNING, wraplength=760, justify="left").pack(anchor="w", pady=(14, 0))
        self._current_beta = 0.0; self._portfolio_value = 0.0
        self.refresh()

    def _set_target(self, value: float) -> None:
        self.target_beta.set(value); self.recalculate()

    def _contract_changed(self) -> None:
        self._load_held_position(); self.recalculate()

    def _load_held_position(self) -> None:
        if not self.owner.get():
            return
        position = load_hedge_position(storage_paths()["decision_database"], self.owner.get(), self.contract.get())
        self.held_contracts.set(f"{position.contracts:g}" if position is not None else "0")

    def save_held_position(self) -> None:
        if not self.owner.get():
            return
        try:
            contracts = float(self.held_contracts.get())
            index_points = float(self.index_points.get())
        except ValueError:
            messagebox.showerror("無法儲存部位", "請輸入正確的口數與台指期點數。", parent=self.winfo_toplevel()); return
        save_hedge_position(storage_paths()["decision_database"], self.owner.get(), self.contract.get(), contracts, index_points, datetime.now().astimezone())
        self.recalculate()

    def refresh(self) -> None:
        ledger = calculate_holdings(storage_paths()["decision_database"])
        positions = [Position(x.owner, x.symbol, x.shares, x.average_cost, x.current_price, datetime.now().astimezone()) for x in ledger if x.current_price is not None]
        if not positions:
            positions = load_positions_csv(Path("data/sample_positions.csv"))
            self._using_sample_data = True
            self.sample_data_warn.configure(text="⚠ 目前顯示的是示範資料（非您的真實持股），請先在「持股管理」新增交易並設定現價")
        else:
            self._using_sample_data = False
            self.sample_data_warn.configure(text="")
        owners = sorted({x.owner for x in positions}); self.owner["values"] = owners
        if owners and self.owner.get() not in owners: self.owner.set(owners[0])
        if not owners:
            self.summary.configure(text="尚無可用持股資料。"); return
        metadata = load_security_metadata(storage_paths()["history_database"], symbols=[p.symbol for p in positions])
        settings = load_risk_preferences(storage_paths()["decision_database"], self.owner.get())
        report = assess_owner_portfolio(self.owner.get(), positions, metadata, settings)
        self._current_beta = report.portfolio_beta; self._portfolio_value = report.total_market_value
        self.summary.configure(text=f"組合市值：{report.total_market_value:,.0f}　目前組合 Beta：{report.portfolio_beta:.2f}（僅計入股票持股，未計入已避險期貨）")
        self._load_held_position()
        self.recalculate()

    def recalculate(self) -> None:
        self.target_label.configure(text=f"{self.target_beta.get():.2f}")
        if self._portfolio_value <= 0:
            self.result.configure(text=""); self.formula.configure(text=""); return
        try:
            index_points = float(self.index_points.get())
            held_contracts = float(self.held_contracts.get())
            suggestion = suggest_hedge(self._portfolio_value, self._current_beta, self.target_beta.get(), index_points, self.contract.get(), held_contracts)
        except ValueError:
            self.result.configure(text="請輸入正確的台指期點數與持有口數"); self.formula.configure(text=""); return
        if suggestion.direction == "不需操作":
            self.result.configure(text="目前 Beta 已達目標，不需操作")
        else:
            self.result.configure(text=f"{suggestion.direction} {suggestion.contracts:g} 口 {CONTRACT_LABELS[self.contract.get()]}")
        self.formula.configure(text=suggestion.formula)


def _refresh_active_tab_on_change(notebook: ttk.Notebook) -> None:
    """A tab's own data (e.g. 自選追蹤's score/decision columns) can go stale
    while the user is on a different tab (e.g. saving a factor score in 個股
    評分輸入) -- refresh() only re-ran on that tab's own buttons before, so
    switching back showed the old state until you happened to click 重新整理.
    Auto-refresh whichever tab becomes visible instead, for every tab that
    already defines a refresh() (a few, like 個股評分輸入, are query-driven
    and have nothing to refresh until the user looks something up)."""
    def _on_tab_changed(_event: object) -> None:
        widget = notebook.nametowidget(notebook.select())
        refresh = getattr(widget, "refresh", None)
        if callable(refresh):
            refresh()
    notebook.bind("<<NotebookTabChanged>>", _on_tab_changed)


class PortfolioDecisionFrame(ttk.Frame):
    """One portfolio workspace: inspect risk, change rules, then review allocation."""
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        notebook = ttk.Notebook(self); notebook.pack(fill="both", expand=True)
        for frame, title in ((PortfolioRiskFrame(notebook), "風險總覽"), (RiskSettingsFrame(notebook), "⚙ 風險設定"), (AllocationFrame(notebook), "🎯 配置建議"), (HedgeAdviceFrame(notebook), "🔧 避險建議")):
            notebook.add(frame, text=title)
        _refresh_active_tab_on_change(notebook)

class NotificationCenterFrame(ttk.Frame):
    """Proactive notifications: periodic watchlist target/stop scans plus data-update outcomes.

    Previously fully passive -- a user had to open the app and click into a
    specific tab to discover a trigger or an update failure. This tab keeps a
    durable log and also drives a periodic background scan (see
    _schedule_periodic_check) so a hit is caught even if this tab is never opened.
    """
    CHECK_INTERVAL_MS = 15 * 60 * 1000  # 15 minutes
    CATEGORY_LABELS = {"watchlist_trigger": "自選股觸價", "data_update": "資料更新"}

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12)
        controls = ttk.Frame(self); controls.pack(fill="x")
        ttk.Button(controls, text="立即檢查自選股觸價", style="Primary.TButton", command=self.check_now).pack(side="left")
        ttk.Button(controls, text="重新整理", command=self.refresh).pack(side="left", padx=(6, 0))
        self.status = ttk.Label(self, style="Muted.TLabel"); self.status.pack(anchor="w", pady=(8, 4))
        ttk.Label(
            self, style="Muted.TLabel", wraplength=900, justify="left",
            text="系統每 15 分鐘自動檢查一次自選股是否觸及鎖定的目標／停損價，並在資料更新完成時記錄結果；"
                 "會嘗試發出 Windows 通知，但通知本身為盡力而為（best-effort）——即使通知未成功跳出，事件仍完整記錄於下方清單，不會遺漏。",
        ).pack(anchor="w", pady=(0, 8))
        columns = ("time", "category", "symbol", "message")
        self.table = ttk.Treeview(self, columns=columns, show="headings", height=16)
        for key, label, width in (("time", "時間", 140), ("category", "類別", 100), ("symbol", "代號", 80), ("message", "訊息", 560)):
            self.table.heading(key, text=label); self.table.column(key, width=width, anchor="w" if key == "message" else "center")
        self.table.tag_configure("gain", foreground=ui_theme.GAIN)
        self.table.tag_configure("loss", foreground=ui_theme.LOSS)
        self.table.tag_configure("error", foreground=ui_theme.ERROR)
        self.table.tag_configure("neutral", foreground=ui_theme.NEUTRAL)
        self.table.pack(fill="both", expand=True)
        self.refresh()
        self._schedule_periodic_check()

    @staticmethod
    def _row_tag(record) -> str:
        if record.message.startswith("停利"):
            return "gain"
        if record.message.startswith("停損"):
            return "loss"
        if "失敗" in record.message or "尚未接入" in record.message or "尚未完成" in record.message:
            return "error"
        return "neutral"

    def check_now(self) -> None:
        fired = check_watchlist_triggers(storage_paths()["decision_database"], datetime.now().astimezone())
        self.status.configure(text=f"檢查完成（{datetime.now().strftime('%H:%M:%S')}）：{f'發現 {len(fired)} 筆新觸發' if fired else '沒有新的觸發事件'}。")
        self.refresh()

    def refresh(self) -> None:
        self.table.delete(*self.table.get_children())
        for record in list_notifications(storage_paths()["decision_database"]):
            self.table.insert("", "end", tags=(self._row_tag(record),), values=(record.triggered_at.strftime("%Y-%m-%d %H:%M"), self.CATEGORY_LABELS.get(record.category, record.category), record.symbol, record.message))

    def _schedule_periodic_check(self) -> None:
        self.after(self.CHECK_INTERVAL_MS, self._periodic_check)

    def _periodic_check(self) -> None:
        try:
            check_watchlist_triggers(storage_paths()["decision_database"], datetime.now().astimezone())
        except Exception:
            pass
        self.refresh()
        self._schedule_periodic_check()


class PriceChartFrame(ttk.Frame):
    """Real daily close-price line chart for one stock, switchable between
    近30/60/180天、近1年 windows. Daily-granularity only -- see
    price_chart_data.py: no intraday/minute data source is wired into this app."""
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12)
        controls = ttk.Frame(self); controls.pack(fill="x")
        ttk.Label(controls, text="股票代號或名稱").pack(side="left")
        self.symbol_entry = ttk.Entry(controls, width=16); self.symbol_entry.pack(side="left", padx=6)
        self.symbol_entry.bind("<Return>", lambda _event: self.show_chart())
        ttk.Label(controls, text="區間").pack(side="left", padx=(12, 0))
        self.window_choice = ttk.Combobox(controls, values=list(WINDOW_CHOICES), state="readonly", width=10)
        self.window_choice.pack(side="left", padx=6); self.window_choice.set("近180天")
        ttk.Button(controls, text="顯示線圖", style="Primary.TButton", command=self.show_chart).pack(side="left", padx=(6, 0))
        self.status = ttk.Label(self, foreground="#555555", wraplength=900, justify="left"); self.status.pack(anchor="w", pady=(6, 0))
        self.chart_area = ttk.Frame(self); self.chart_area.pack(fill="both", expand=True, pady=(8, 0))

    def show_chart(self) -> None:
        query = self.symbol_entry.get().strip()
        if not query:
            messagebox.showinfo("個股線圖", "請輸入股票代號或名稱。", parent=self.winfo_toplevel()); return
        history_database = storage_paths()["history_database"]
        try:
            symbol = resolve_symbol(history_database, query)
        except ValueError as error:
            messagebox.showerror("個股線圖", str(error), parent=self.winfo_toplevel()); return
        window_label = self.window_choice.get() or "近180天"
        closes = load_recent_closes(history_database, symbol, WINDOW_CHOICES[window_label])
        for child in self.chart_area.winfo_children(): child.destroy()
        if not closes:
            self.status.configure(text=f"{symbol} 本機尚無日線資料，請先在「資料管理」回補歷史資料。")
            return
        self.status.configure(text=f"{symbol}　共 {len(closes)} 個交易日｜{closes[0].trading_date} ~ {closes[-1].trading_date}")
        price_chart(self.chart_area, closes, symbol, window_label)


class StockAiApp(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master); master.title("Stock AI 台股分析工具"); self.pack(fill="both", expand=True); notebook = ttk.Notebook(self); notebook.pack(fill="both", expand=True)
        self.data_management_frame = DataManagementFrame(notebook)
        for frame, title in ((HoldingsManager(notebook), "📊 持股管理"), (WatchlistApp(notebook), "⭐ 自選追蹤"), (PortfolioDecisionFrame(notebook), "🛡 組合風險與配置"), (FactorScoreApp(notebook), "📝 個股評分輸入"), (PriceChartFrame(notebook), "📈 個股線圖"), (ShortScreeningApp(notebook), "📉 放空篩選（實驗性）"), (BacktestApp(notebook), "🧪 技術面回測驗證"), (self.data_management_frame, "🗄 資料管理"), (JudgementWeightFrame(notebook), "⚖ 判斷機制權重"), (NotificationCenterFrame(notebook), "🔔 通知中心")): notebook.add(frame, text=title)
        _refresh_active_tab_on_change(notebook)

    def run_startup_checks_in_background(self) -> None:
        """Kick off the broker-fee/daily-data startup checks after the window
        is already visible and interactive, instead of blocking mainloop from
        ever starting -- these are real network calls (broker fee page, and
        potentially a full TWSE/TPEx/VIX update if today's data isn't fetched
        yet) that used to hold up window creation itself for anywhere from a
        few seconds to several minutes."""
        self.data_management_frame.begin_startup_check()


def _show_dashboard(root: tk.Tk, warn_if_empty: bool = False) -> None:
    for child in root.winfo_children(): child.destroy()
    paths=storage_paths()
    if warn_if_empty and not paths["history_database"].exists() and not paths["decision_database"].exists():
        # A returning user (storage.json already existed before this launch)
        # should already have accumulated some data; both databases missing
        # usually means the configured folder was moved/renamed/misconfigured,
        # not that this is genuinely a fresh install.
        messagebox.showwarning(
            "資料位置檢查",
            f"目前設定的資料夾：\n{paths['root']}\n\n裡面找不到既有的歷史或決策資料庫。如果這不是你第一次使用本程式，代表資料夾路徑可能設定錯誤，請至「資料管理」分頁確認，或重新設定資料位置。",
            parent=root,
        )
    app = StockAiApp(root)
    # Startup is intentionally conservative: validate first, then download only
    # once the scheduled public EOD time has passed or archive checks fail.
    # Deferred to a background thread (see run_startup_checks_in_background)
    # so the window itself never waits on it.
    app.run_startup_checks_in_background()


def main() -> None:
    # PyInstaller extracts bundled config/sample assets to _MEIPASS.  Switch
    # only the process working directory; user databases still use D:\stock_AI.
    if getattr(sys, "frozen", False):
        os.chdir(sys._MEIPASS)
    root = tk.Tk(); root.minsize(1360, 700); root.geometry("1360x760")
    ui_theme.apply(root)
    if has_user_storage_config(): _show_dashboard(root, warn_if_empty=True)
    else: StorageSetupApp(root, on_complete=lambda: _show_dashboard(root, warn_if_empty=False))
    root.mainloop()


if __name__ == "__main__": main()
