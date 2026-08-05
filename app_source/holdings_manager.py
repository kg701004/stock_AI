"""Interactive transaction-ledger holding management for the integrated desktop app."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import ui_theme
from storage_paths import storage_paths
from broker_discount import load_discount, save_discount
from broker_fees import estimate
from factor_score_store import seed_default_factor_scores
from historical_storage import latest_close_price
from security_catalog import is_etf, resolve
from transaction_ledger import Transaction, add_transaction, calculate_holdings, delete_transaction, list_transactions, owner_summary, set_current_price


class HoldingsManager(ttk.Frame):
    """Allow manual buy/sell/price entry and show owner-specific accounting results."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=10)
        self.database = storage_paths()["decision_database"]
        self._build()
        self.refresh()

    def _build(self) -> None:
        actions = ttk.Frame(self)
        actions.pack(fill="x")
        ttk.Button(actions, text="新增買入", style="Primary.TButton", command=lambda: self.add_trade("BUY")).pack(side="left", padx=(0, 6))
        for label, command in (("新增賣出", lambda: self.add_trade("SELL")), ("更新現價", self.update_price), ("重新整理", self.refresh)):
            ttk.Button(actions, text=label, command=command).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="刪除選取交易", style="Danger.TButton", command=self.delete_selected).pack(side="left", padx=(0, 6))
        ttk.Label(self, text="每筆買賣獨立保存；成本採加權平均，已實現與未實現損益分開統計；紅字為獲利、綠字為虧損（台股慣例）。", style="Muted.TLabel").pack(anchor="w", pady=(8, 4))
        self.holdings = ttk.Treeview(self, columns=("owner", "symbol", "shares", "avg", "price", "unrealized", "unrealized_pct", "realized", "fees"), show="headings", height=8)
        labels = {"owner": "持有人", "symbol": "代號", "shares": "股數", "avg": "平均成本", "price": "現價", "unrealized": "未實現損益", "unrealized_pct": "未實現%", "realized": "已實現損益", "fees": "累計費用"}
        for name in self.holdings["columns"]:
            self.holdings.heading(name, text=labels[name]); self.holdings.column(name, width=105, anchor="center")
        self.holdings.pack(fill="x")
        self.holdings.bind("<<TreeviewSelect>>", self.show_transactions)
        self._configure_pl_tags(self.holdings)
        ttk.Label(self, text="選取持股的交易明細").pack(anchor="w", pady=(10, 2))
        self.transactions = ttk.Treeview(self, columns=("id", "time", "side", "shares", "price", "fee", "note"), show="headings", height=8)
        for name, label in {"id": "ID", "time": "時間", "side": "買賣", "shares": "股數", "price": "價格", "fee": "費用", "note": "備註"}.items():
            self.transactions.heading(name, text=label); self.transactions.column(name, width=125, anchor="center")
        self.transactions.pack(fill="both", expand=True)
        ui_theme.stripe(self.transactions)
        self.summary = ttk.Label(self, text="")
        self.summary.pack(anchor="w", pady=(6, 0))

    @staticmethod
    def _configure_pl_tags(tree: ttk.Treeview) -> None:
        """Whole-row color coding for unrealized P/L, Taiwan convention (red=gain, green=loss)."""
        tree.tag_configure("gain", foreground=ui_theme.GAIN)
        tree.tag_configure("loss", foreground=ui_theme.LOSS)
        tree.tag_configure("neutral", foreground=ui_theme.NEUTRAL)

    def _ask(self, prompt: str, initial: str = "") -> str | None:
        return simpledialog.askstring("持股交易", prompt, initialvalue=initial, parent=self.winfo_toplevel())

    def add_trade(self, side: str) -> None:
        """Single form replacing the old 6-popup sequence for one buy/sell entry."""
        owners = sorted({item.owner for item in calculate_holdings(self.database)})
        top = self.winfo_toplevel()
        dialog = tk.Toplevel(top); dialog.title("新增買入" if side == "BUY" else "新增賣出"); dialog.transient(top); dialog.grab_set(); dialog.configure(background=ui_theme.BACKGROUND)
        form = ttk.Frame(dialog, padding=12); form.pack(fill="both", expand=True)

        owner_var = tk.StringVar(value=owners[0] if owners else "")
        symbol_var = tk.StringVar(); shares_var = tk.StringVar(); price_var = tk.StringVar()
        day_trade_var = tk.BooleanVar(value=False)
        traded_at_var = tk.StringVar(value=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"))
        note_var = tk.StringVar()
        discount_var = tk.StringVar(value=f"{load_discount(self.database, owner_var.get()) * 10:g}" if owner_var.get() else "10")

        ttk.Label(form, text="持有人").grid(row=0, column=0, sticky="w", pady=3)
        owner_combo = ttk.Combobox(form, textvariable=owner_var, values=owners, width=22); owner_combo.grid(row=0, column=1, sticky="w")
        ttk.Label(form, text="股票代號或名稱").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=symbol_var, width=24).grid(row=1, column=1, sticky="w")
        ttk.Label(form, text="股數（1 張＝1000 股）").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=shares_var, width=24).grid(row=2, column=1, sticky="w")
        ttk.Label(form, text="成交價格").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=price_var, width=24).grid(row=3, column=1, sticky="w")
        ttk.Label(form, text="手續費折數（10＝不打折）").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=discount_var, width=24).grid(row=4, column=1, sticky="w")

        estimate_label = ttk.Label(form, text="輸入股數與價格即可試算手續費／金額", foreground="#555555")
        estimate_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 6))

        if side == "SELL":
            ttk.Checkbutton(form, text="現股當沖", variable=day_trade_var).grid(row=6, column=0, columnspan=2, sticky="w")

        ttk.Label(form, text="交易日期時間").grid(row=7, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=traded_at_var, width=24).grid(row=7, column=1, sticky="w")
        ttk.Label(form, text="備註（選填）").grid(row=8, column=0, sticky="w", pady=3)
        ttk.Entry(form, textvariable=note_var, width=24).grid(row=8, column=1, sticky="w")

        def owner_selected(_event: object = None) -> None:
            if owner_var.get():
                discount_var.set(f"{load_discount(self.database, owner_var.get()) * 10:g}")
        owner_combo.bind("<<ComboboxSelected>>", owner_selected)

        def update_estimate(*_args) -> None:
            try:
                shares_value = int(shares_var.get()); price_value = float(price_var.get())
                symbol_guess = symbol_var.get().strip()
                discount_value = float(discount_var.get()) / 10
                fee = estimate(price_value, shares_value, side, is_etf(symbol_guess) if symbol_guess else False, day_trade_var.get(), discount_value)
                gross = price_value * shares_value
                total = gross + fee.total if side == "BUY" else gross - fee.total
                estimate_label.configure(text=f"預估手續費 {fee.total:,.0f}；預估{'成交金額' if side == 'BUY' else '淨收入'} {total:,.0f}")
            except (ValueError, ZeroDivisionError):
                estimate_label.configure(text="輸入股數與價格即可試算手續費／金額")
        for var in (shares_var, price_var, symbol_var, day_trade_var, discount_var):
            var.trace_add("write", update_estimate)

        def confirm() -> None:
            owner = owner_var.get().strip()
            if not owner or not symbol_var.get().strip() or not shares_var.get().strip() or not price_var.get().strip():
                messagebox.showerror("欄位不完整", "持有人、股票代號、股數、價格皆為必填。", parent=dialog); return
            try:
                symbol = resolve(storage_paths()["history_database"], symbol_var.get().strip())
                moment = datetime.fromisoformat(traded_at_var.get().strip()).astimezone()
                shares = int(shares_var.get()); price = float(price_var.get())
                discount_value = float(discount_var.get()) / 10
                fee = estimate(price, shares, side, is_etf(symbol), day_trade_var.get(), discount_value)
                add_transaction(self.database, Transaction(None, owner, symbol, moment, side, shares, price, fee.total, note_var.get()))
                save_discount(self.database, owner, discount_value)
                if side == "BUY":
                    # New position -> make sure 配置建議 etc. have something to
                    # score it with instead of silently excluding it; see
                    # factor_score_store.seed_default_factor_scores for the
                    # honesty safeguard that flags this as unconfirmed.
                    seed_default_factor_scores(self.database, storage_paths()["history_database"], symbol, moment)
                dialog.destroy()
                self.refresh()
            except ValueError as error:
                messagebox.showerror("無法記錄交易", str(error), parent=dialog)

        buttons = ttk.Frame(form); buttons.grid(row=9, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(buttons, text="確認新增", style="Primary.TButton", command=confirm).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="left")
        dialog.bind("<Return>", lambda _event: confirm())

    def update_price(self) -> None:
        """本機沒有即時報價來源，價格一律由使用者輸入或確認；若本機歷史
        資料庫已有這檔股票的最新收盤價，會先帶入當作預設值。"""
        selected = self.holdings.selection()
        if not selected:
            messagebox.showinfo("請先選取", "請先在持股列表選取一檔股票。", parent=self.winfo_toplevel()); return
        symbol = str(self.holdings.item(selected[0])["values"][1])
        latest = latest_close_price(storage_paths()["history_database"], symbol)
        initial = "" if latest is None else f"{latest[0]:g}"
        prompt = f"{symbol} 最新盤後價格" + ("" if latest is None else f"（本機最新收盤 {latest[0]:g}，{latest[1]}）")
        price = self._ask(prompt, initial)
        if price is None:
            return
        try:
            set_current_price(self.database, symbol, float(price), datetime.now().astimezone())
            self.refresh()
        except ValueError as error:
            messagebox.showerror("無法更新價格", str(error), parent=self.winfo_toplevel())

    def delete_selected(self) -> None:
        selected = self.transactions.selection()
        if not selected:
            return
        transaction_id = int(self.transactions.item(selected[0])["values"][0])
        if messagebox.askyesno("刪除交易", "確定刪除選取交易？持股與損益將重新計算。", parent=self.winfo_toplevel()):
            delete_transaction(self.database, transaction_id)
            self.refresh()

    def refresh(self) -> None:
        for tree in (self.holdings, self.transactions):
            tree.delete(*tree.get_children())
        holdings = calculate_holdings(self.database)
        for item in holdings:
            tag = "neutral" if item.unrealized_profit is None else ("gain" if item.unrealized_profit > 0 else "loss" if item.unrealized_profit < 0 else "neutral")
            self.holdings.insert("", "end", tags=(tag,), values=(item.owner, item.symbol, item.shares, f"{item.average_cost:.2f}", "—" if item.current_price is None else f"{item.current_price:.2f}", "—" if item.unrealized_profit is None else f"{item.unrealized_profit:,.0f}", "—" if item.unrealized_profit_pct is None else f"{item.unrealized_profit_pct:.2f}%", f"{item.realized_profit:,.0f}", f"{item.total_fees:,.0f}"))
        owners = sorted({item.owner for item in holdings})
        summaries = [f"{owner}：未實現 {owner_summary(self.database, owner)['unrealized_profit']:,.0f}／已實現 {owner_summary(self.database, owner)['realized_profit']:,.0f}" for owner in owners]
        self.summary.configure(text="　".join(summaries) or "尚未輸入交易紀錄。")

    def show_transactions(self, _event: object) -> None:
        self.transactions.delete(*self.transactions.get_children())
        selected = self.holdings.selection()
        if not selected:
            return
        owner, symbol = (str(value) for value in self.holdings.item(selected[0])["values"][:2])
        for index, item in enumerate(list_transactions(self.database, owner, symbol)):
            self.transactions.insert("", "end", tags=(ui_theme.stripe_tag(index),), values=(item.id, item.traded_at.isoformat(sep=" ", timespec="minutes"), "買入" if item.side == "BUY" else "賣出", item.shares, f"{item.price:.2f}", f"{item.fee:.0f}", item.note))
