"""GUI screen for the experimental short-candidate screening module.

Deliberately labelled experimental in the UI: chip-side data (margin ratio,
securities lending, short-squeeze/buy-in dates) is not supported yet, so any
score shown here is partial by design, not a bug.
"""
from __future__ import annotations

import sqlite3
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, simpledialog, ttk

import ui_theme
from security_catalog import resolve
from short_screening import assess_short_candidate
from short_positions import close_position, list_positions, open_position
from storage_paths import storage_paths
from transaction_ledger import calculate_holdings


class ShortScreeningApp(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=12)
        ttk.Label(
            self,
            text="放空篩選為實驗性功能：技術面與財務面訊號已實作，但券資比／借券／除權息強制回補等籌碼與軋空風險資料，"
                 "本系統目前沒有對應的資料匯入器，一律誠實回報「尚未支援」。結果僅供研究參考，不構成任何操作建議，本系統不執行任何下單或放空委託。",
            foreground=ui_theme.WARNING, wraplength=780, justify="left",
        ).pack(anchor="w")

        lookup = ttk.Frame(self); lookup.pack(fill="x", pady=(10, 0))
        ttk.Label(lookup, text="股票代號或名稱").pack(side="left")
        self.query = ttk.Entry(lookup, width=20); self.query.pack(side="left", padx=(4, 8))
        ttk.Button(lookup, text="查詢", command=self.lookup_symbol).pack(side="left")
        self.resolved = ttk.Label(lookup, text="尚未查詢股票", foreground="#555555"); self.resolved.pack(side="left", padx=(12, 0))
        self._resolved_symbol: str | None = None

        ttk.Label(self, text="技術面空頭訊號", font=("Microsoft JhengHei UI", 10, "bold")).pack(anchor="w", pady=(14, 2))
        self.technical_label = ttk.Label(self, text="請先查詢股票。", foreground="#555555", wraplength=780, justify="left")
        self.technical_label.pack(anchor="w")

        ttk.Label(self, text="財務惡化訊號（非正式 Z-Score，僅供參考）", font=("Microsoft JhengHei UI", 10, "bold")).pack(anchor="w", pady=(14, 2))
        self.financial_label = ttk.Label(self, text="", foreground="#555555", wraplength=780, justify="left")
        self.financial_label.pack(anchor="w")

        ttk.Label(self, text="籌碼與軋空風險", font=("Microsoft JhengHei UI", 10, "bold")).pack(anchor="w", pady=(14, 2))
        self.chip_label = ttk.Label(self, text="", foreground=ui_theme.WARNING, wraplength=780, justify="left")
        self.chip_label.pack(anchor="w")

        ttk.Separator(self).pack(fill="x", pady=(14, 10))
        position_header = ttk.Frame(self); position_header.pack(fill="x")
        ttk.Label(position_header, text="持有人").pack(side="left")
        self.owner = ttk.Combobox(position_header, width=18); self.owner.pack(side="left", padx=(4, 8)); self.owner.bind("<<ComboboxSelected>>", lambda _: self.refresh_positions())
        ttk.Button(position_header, text="記錄放空部位", style="Primary.TButton", command=self.record_position).pack(side="left", padx=(0, 6))
        ttk.Button(position_header, text="回補平倉", style="Danger.TButton", command=self.close_selected).pack(side="left", padx=(0, 6))
        ttk.Button(position_header, text="重新整理", command=self.refresh_positions).pack(side="left")
        ttk.Label(self, text="放空部位為使用者自行記錄，本系統不執行任何放空委託；未實現損益僅供參考，需自行留意實際保證金與回補風險。", foreground=ui_theme.WARNING, wraplength=780, justify="left").pack(anchor="w", pady=(6, 6))

        columns = ("symbol", "shares", "entry", "current", "unrealized", "opened", "status")
        self.positions = ttk.Treeview(self, columns=columns, show="headings", height=6)
        for key, label in zip(columns, ("股票代號", "股數", "進場價", "現價", "未實現／已實現損益", "開倉時間", "狀態")):
            self.positions.heading(key, text=label); self.positions.column(key, width=110, anchor="center")
        self.positions.pack(fill="x")
        self.positions.tag_configure("gain", foreground=ui_theme.GAIN)
        self.positions.tag_configure("loss", foreground=ui_theme.LOSS)
        self.positions.tag_configure("neutral", foreground=ui_theme.NEUTRAL)

        self.refresh_positions()

    def lookup_symbol(self) -> None:
        query = self.query.get().strip()
        if not query:
            return
        try:
            symbol = resolve(storage_paths()["history_database"], query)
        except ValueError as error:
            messagebox.showerror("查詢失敗", str(error), parent=self.winfo_toplevel()); return
        self._resolved_symbol = symbol
        self.resolved.configure(text=f"已選定：{symbol}")
        result = assess_short_candidate(storage_paths()["history_database"], storage_paths()["decision_database"], symbol)
        technical_prefix = "資料不足，無法評估。" if result.technical_score is None else f"分數 {result.technical_score:.0f}／100。"
        self.technical_label.configure(text=technical_prefix + " " + " ".join(result.technical_notes))
        financial_prefix = "資料不足，無法評估。" if result.financial_score is None else f"分數 {result.financial_score:.0f}／100。"
        self.financial_label.configure(text=financial_prefix + " " + " ".join(result.financial_notes))
        self.chip_label.configure(text=" ".join(result.unsupported_warnings))

    def _current_prices(self) -> dict[str, float]:
        connection = sqlite3.connect(storage_paths()["decision_database"])
        try:
            try:
                return dict(connection.execute("SELECT symbol, price FROM current_prices"))
            except sqlite3.OperationalError:
                return {}
        finally:
            connection.close()

    def record_position(self) -> None:
        owners = sorted({item.owner for item in calculate_holdings(storage_paths()["decision_database"])})
        top = self.winfo_toplevel()
        dialog = tk.Toplevel(top); dialog.title("記錄放空部位"); dialog.transient(top); dialog.grab_set(); dialog.configure(background=ui_theme.BACKGROUND)
        form = ttk.Frame(dialog, padding=12); form.pack(fill="both", expand=True)

        owner_var = tk.StringVar(value=self.owner.get() or (owners[0] if owners else ""))
        symbol_var = tk.StringVar(value=self._resolved_symbol or "")
        shares_var = tk.StringVar(); price_var = tk.StringVar(); note_var = tk.StringVar()

        ttk.Label(form, text="持有人").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Combobox(form, textvariable=owner_var, values=owners, width=22).grid(row=0, column=1, sticky="w")
        ttk.Label(form, text="股票代號或名稱").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=symbol_var, width=24).grid(row=1, column=1, sticky="w")
        ttk.Label(form, text="放空股數").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=shares_var, width=24).grid(row=2, column=1, sticky="w")
        ttk.Label(form, text="進場（放空）價格").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=price_var, width=24).grid(row=3, column=1, sticky="w")
        ttk.Label(form, text="備註（選填）").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=note_var, width=24).grid(row=4, column=1, sticky="w")

        def confirm() -> None:
            owner = owner_var.get().strip()
            if not owner or not symbol_var.get().strip() or not shares_var.get().strip() or not price_var.get().strip():
                messagebox.showerror("欄位不完整", "持有人、股票代號、股數、價格皆為必填。", parent=dialog); return
            try:
                symbol = resolve(storage_paths()["history_database"], symbol_var.get().strip())
                shares = int(shares_var.get()); price = float(price_var.get())
                open_position(storage_paths()["decision_database"], owner, symbol, shares, price, datetime.now().astimezone(), note_var.get())
                dialog.destroy()
                self.owner.set(owner)
                self.refresh_positions()
            except ValueError as error:
                messagebox.showerror("無法記錄放空部位", str(error), parent=dialog)

        buttons = ttk.Frame(form); buttons.grid(row=5, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(buttons, text="確認記錄", style="Primary.TButton", command=confirm).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="left")
        dialog.bind("<Return>", lambda _event: confirm())

    def close_selected(self) -> None:
        selected = self.positions.selection()
        if not selected:
            messagebox.showinfo("請先選取", "請先在放空部位列表選取一筆部位。", parent=self.winfo_toplevel()); return
        position_id = int(self.positions.item(selected[0])["tags"][0])
        price = simpledialog.askstring("回補平倉", "回補（買回）價格", parent=self.winfo_toplevel())
        if price is None:
            return
        try:
            close_position(storage_paths()["decision_database"], position_id, float(price), datetime.now().astimezone())
            self.refresh_positions()
        except ValueError as error:
            messagebox.showerror("無法回補平倉", str(error), parent=self.winfo_toplevel())

    def refresh_positions(self) -> None:
        database = storage_paths()["decision_database"]
        owners = sorted({item.owner for item in list_positions(database)} | {item.owner for item in calculate_holdings(database)})
        self.owner["values"] = owners
        if owners and self.owner.get() not in owners:
            self.owner.set(owners[0])
        self.positions.delete(*self.positions.get_children())
        if not self.owner.get():
            return
        prices = self._current_prices()
        for item in list_positions(database, self.owner.get()):
            current = prices.get(item.symbol)
            pl_value: float | None
            if item.is_open:
                pl_value = None if current is None else item.unrealized_profit(current)
                profit = "—" if pl_value is None else f"{pl_value:,.0f}（未實現）"
                status = "持有中"
            else:
                pl_value = item.realized_profit()
                profit = f"{pl_value:,.0f}（已實現）"
                status = "已回補"
            self.positions.insert(
                "", "end", tags=(str(item.id), "neutral" if pl_value is None else ("gain" if pl_value > 0 else "loss" if pl_value < 0 else "neutral")),
                values=(item.symbol, item.shares, f"{item.entry_price:.2f}", "—" if current is None else f"{current:.2f}", profit, item.opened_at.strftime("%Y-%m-%d %H:%M"), status),
            )
