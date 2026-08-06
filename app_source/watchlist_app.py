"""Ownerless watchlist: user reference price plus program-generated target/stop."""
from __future__ import annotations
import sqlite3
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
import ui_theme
from factor_score_app import FACTOR_LABELS
from factor_score_store import load_all_current_assessments, seed_default_factor_scores
from historical_storage import latest_close_price
from security_catalog import lookup_name, resolve
from storage_paths import storage_paths
from visualization import factor_heatmap
from transaction_ledger import set_current_price
from watchlist_decision import calculate, evaluate
from multi_layer_risk import LayeredInputs, evaluate as evaluate_layers
from technical_layers import calculate as calculate_technical_layers
from technical_factor import load_adjusted_bars
from technical_signal import calculate as calculate_technical_signal
from watchlist_repository import add_item, delete_item, list_items, update_levels
from weighted_analysis import assess_stock, load_weight_config


def resolve_symbol_and_name(history_database: Path, query: str) -> tuple[str, str]:
    """Resolve a four-digit code or a name (exact or unambiguous partial) to
    (symbol, name), so the user only has to type whichever one they know."""
    symbol = resolve(history_database, query)
    return symbol, lookup_name(history_database, symbol) or symbol


def seed_current_price_from_history(decision_database: Path, history_database: Path, symbol: str) -> float | None:
    """If this symbol has no 現價 yet, seed it from the latest locally
    archived daily bar so a freshly-added watchlist item isn't stuck at "--"
    for a stock we already have real data for. Never overwrites an existing
    entry -- that could be a more current, manually-entered price."""
    existing = dict(_read_current_prices(decision_database))
    if symbol in existing:
        return None
    latest = latest_close_price(history_database, symbol)
    if latest is None:
        return None
    close_price, _trading_date = latest
    set_current_price(decision_database, symbol, close_price, datetime.now().astimezone())
    return close_price


def _read_current_prices(database: Path) -> dict[str, float]:
    connection = sqlite3.connect(database)
    try:
        try:
            return dict(connection.execute("SELECT symbol, price FROM current_prices"))
        except sqlite3.OperationalError:
            return {}
    finally:
        connection.close()


class WatchlistApp(ttk.Frame):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, padding=10); self.database=storage_paths()["decision_database"]; self.tooltip=None; self.tip_job=None
        self.scores={}; self.details={}; self._build(); self.refresh()
    def _reload_scores(self):
        # Recomputed on every refresh (not just once at startup) so scores
        # entered via the "個股評分輸入" screen show up without a restart.
        config=load_weight_config(Path("config/analysis_weights.json"))
        self.scores={symbol: (assess_stock(row, config), row.risk_score) for symbol, row in load_all_current_assessments(self.database, storage_paths()["history_database"]).items()}
    DECISION_TAGS = {"停利": "gain", "移動停利": "gain", "停利（轉弱）": "gain", "停損": "loss", "續抱／觀察": "neutral", "觀察風險": "warning", "待輸入現價／評分": "muted"}
    def _build(self):
        bar=ttk.Frame(self); bar.pack(fill="x")
        ttk.Button(bar,text="新增自選",style="Primary.TButton",command=self.add).pack(side="left",padx=(0,6))
        for label, fn in (("更新現價",self.update_price),("重新分析全部",self.reanalyse),("重新整理",self.refresh)): ttk.Button(bar,text=label,command=fn).pack(side="left",padx=(0,6))
        ttk.Button(bar,text="刪除",style="Danger.TButton",command=self.delete).pack(side="left",padx=(0,6))
        ttk.Label(self,text="參考價由使用者填寫；目標價與停損價由目前現價、綜合分數與風險分數自動計算。停留在「判斷」欄一秒可查看條件；紅字＝停利、綠字＝停損（台股慣例）。",style="Muted.TLabel").pack(anchor="w",pady=(8,4))
        cols=("id","symbol","name","current","reference","target","stop","score","decision"); self.table=ttk.Treeview(self,columns=cols,show="headings",height=12)
        for key,label in zip(cols,("ID","代號","名稱","現價","參考價","目標價","停損價","分數","判斷")): self.table.heading(key,text=label); self.table.column(key,width=100,anchor="center")
        self.table.pack(fill="both",expand=True); self.table.bind("<Motion>",self.hover); self.table.bind("<Leave>",lambda _:self.hide_tip())
        self.table.tag_configure("gain", foreground=ui_theme.GAIN)
        self.table.tag_configure("loss", foreground=ui_theme.LOSS)
        self.table.tag_configure("warning", foreground=ui_theme.WARNING)
        self.table.tag_configure("neutral", foreground=ui_theme.NEUTRAL)
        self.table.tag_configure("muted", foreground=ui_theme.MUTED)
        self.detail=tk.Text(self,height=7,wrap="word",state="disabled"); self.detail.pack(fill="x",pady=(8,0)); self.table.bind("<<TreeviewSelect>>",self.show_detail)
        ttk.Label(self,text="自選股因子比較",style="Muted.TLabel").pack(anchor="w",pady=(8,2)); self.chart_area=ttk.Frame(self); self.chart_area.pack(fill="x")
    def ask(self,prompt,initial=""):
        return simpledialog.askstring("自選追蹤",prompt,initialvalue=initial,parent=self.winfo_toplevel())
    def add(self):
        """Single form (matching holdings_manager's add_trade) instead of three sequential popups."""
        top=self.winfo_toplevel()
        dialog=tk.Toplevel(top); dialog.title("新增自選股"); dialog.transient(top); dialog.grab_set(); dialog.configure(background=ui_theme.BACKGROUND)
        form=ttk.Frame(dialog,padding=12); form.pack(fill="both",expand=True)
        symbol_var=tk.StringVar(); reference_var=tk.StringVar()
        ttk.Label(form,text="股票代號或名稱").grid(row=0,column=0,sticky="w",pady=3)
        symbol_entry=ttk.Entry(form,textvariable=symbol_var,width=24); symbol_entry.grid(row=0,column=1,sticky="w")
        ttk.Label(form,text="參考價（你的購入或預計購入價）").grid(row=1,column=0,sticky="w",pady=3)
        ttk.Entry(form,textvariable=reference_var,width=24).grid(row=1,column=1,sticky="w")
        def confirm() -> None:
            if not symbol_var.get().strip() or not reference_var.get().strip():
                messagebox.showerror("欄位不完整","股票代號或名稱、參考價皆為必填。",parent=dialog); return
            try:
                symbol,name=resolve_symbol_and_name(storage_paths()["history_database"],symbol_var.get().strip())
            except ValueError as error:
                messagebox.showerror("找不到股票",str(error),parent=dialog); return
            try:
                reference=float(reference_var.get())
                item_id=add_item(self.database,symbol,name,reference,reference,reference,datetime.now().astimezone())
                # Seed 現價 from real local history when we already have some,
                # instead of always leaving a freshly-added stock at "—" until
                # a separate manual "更新現價" click.
                seed_current_price_from_history(self.database,storage_paths()["history_database"],symbol)
                # Seed an initial (neutral-default, clearly flagged as
                # unconfirmed) factor score too, so 分數／判斷 aren't stuck
                # empty until a separate manual visit to 個股評分輸入 -- see
                # factor_score_store.seed_default_factor_scores for the honesty
                # safeguard (SEEDED_NOTE_KEY) that keeps this from ever being
                # mistaken for a factor set a person actually reviewed.
                seed_default_factor_scores(self.database,storage_paths()["history_database"],symbol,datetime.now().astimezone())
                # add_item() leaves target=stop=reference_price as a raw
                # placeholder -- if today's real seeded price is already above
                # a low reference (e.g. an old cost basis), that placeholder
                # falsely reads as "already blown past target" (停利) until
                # the next 重新分析全部. Recompute real levels immediately
                # from the price/score we just seeded, using the same formula
                # 重新分析全部 uses, so the very first render is already correct.
                self._reload_scores()
                price=self.prices().get(symbol); pair=self.scores.get(symbol)
                if price and pair:
                    assessment,risk=pair
                    decision=calculate(reference,price,assessment.final_score,risk)
                    update_levels(self.database,item_id,decision.target_price,decision.stop_price)
                dialog.destroy(); self.refresh()
            except ValueError as error:
                messagebox.showerror("新增失敗",str(error),parent=dialog)
        buttons=ttk.Frame(form); buttons.grid(row=3,column=0,columnspan=2,pady=(12,0))
        ttk.Button(buttons,text="確認新增",style="Primary.TButton",command=confirm).pack(side="left",padx=(0,6))
        ttk.Button(buttons,text="取消",command=dialog.destroy).pack(side="left")
        dialog.bind("<Return>",lambda _event:confirm())
        symbol_entry.focus_set()
    def delete(self):
        selected=self.table.selection()
        if selected: delete_item(self.database,int(self.table.item(selected[0])["values"][0])); self.refresh()
    def update_price(self):
        """本機沒有即時報價來源，價格一律由使用者輸入或確認；若本機歷史
        資料庫已有這檔股票的最新收盤價，會先帶入當作預設值，讓使用者按
        Enter 直接採用，不需要每次都自己查價格再手動打一次數字。"""
        selected=self.table.selection()
        if not selected: return
        symbol=str(self.table.item(selected[0])["values"][1])
        latest=latest_close_price(storage_paths()["history_database"],symbol)
        initial="" if latest is None else f"{latest[0]:g}"
        value=self.ask(f"{symbol} 最新價格" + ("" if latest is None else f"（本機最新收盤 {latest[0]:g}，{latest[1]}）"),initial)
        if value is None:return
        try:set_current_price(self.database,symbol,float(value),datetime.now().astimezone()); self.refresh()
        except ValueError as e: messagebox.showerror("價格錯誤",str(e),parent=self.winfo_toplevel())
    def prices(self):
        return _read_current_prices(self.database)
    def technical(self,symbol):
        """Enable ATR/support/MA layers only when locally archived history exists (ex-dividend adjusted)."""
        try: return calculate_technical_layers(load_adjusted_bars(storage_paths()["history_database"],symbol))
        except (sqlite3.Error,ValueError): return None
    def technical_confirmation(self,symbol):
        """Independent confirmation score; insufficient history never becomes a signal (ex-dividend adjusted)."""
        try: return calculate_technical_signal(load_adjusted_bars(storage_paths()["history_database"],symbol))
        except (sqlite3.Error,ValueError): return None
    def reanalyse(self):
        """Silently doing nothing for a stock missing 現價 or 分數 looks
        exactly like the button not working -- report what happened instead."""
        prices=self.prices(); updated=[]; skipped=[]
        for item in list_items(self.database):
            pair=self.scores.get(item.symbol)
            if pair and prices.get(item.symbol):
                assessment,risk=pair; d=calculate(item.reference_price,prices[item.symbol],assessment.final_score,risk); update_levels(self.database,item.id,d.target_price,d.stop_price)
                updated.append(item.symbol)
            else:
                reason="缺現價與評分" if not pair and not prices.get(item.symbol) else "缺現價" if not prices.get(item.symbol) else "缺評分"
                skipped.append(f"{item.symbol}（{reason}）")
        self.refresh()
        message=f"已更新 {len(updated)} 檔。"
        if skipped:
            message+=f" 跳過 {len(skipped)} 檔：" + "、".join(skipped)
        messagebox.showinfo("重新分析全部",message,parent=self.winfo_toplevel())
    def refresh(self):
        self._reload_scores()
        self.table.delete(*self.table.get_children()); prices=self.prices(); self.details={}
        for item in list_items(self.database):
            price=prices.get(item.symbol); pair=self.scores.get(item.symbol); score="—" if not pair else f"{pair[0].final_score:.1f}"
            if price and pair:
                tech=self.technical(item.symbol); confirmation=self.technical_confirmation(item.symbol); layered=evaluate_layers(LayeredInputs(price,item.reference_price,item.target_price,item.stop_price,atr_stop=None if tech is None else price-2*tech.atr,support=None if tech is None else tech.support,moving_average=None if tech is None else tech.ma20,event_risk=pair[1],technical_score=None if confirmation is None else confirmation.score)); decision=layered.action; technical_text="" if tech is None else f" ATR {tech.atr:.2f}、MA20 {tech.ma20:.2f}、支撐 {tech.support:.2f}、壓力 {tech.resistance:.2f}、相對量 {tech.relative_volume:.2f}、趨勢 {tech.regime}。"; confirmation_text=" 技術確認資料不足，未納入判斷。" if confirmation is None else f" 技術確認：{confirmation.status} {confirmation.score:.1f} 分。{' '.join(confirmation.reasons)}"; self.details[item.id]=f"{'; '.join(layered.triggers)}。有效停損 {layered.effective_stop:.2f}。分析分數 {pair[0].final_score:.1f}、風險分數 {pair[1]:.1f}。{technical_text}{confirmation_text}" + (" " + " ".join(layered.warnings) if layered.warnings else "") + (" " + " ".join(pair[0].warnings) if pair[0].warnings else "")
            else: decision="待輸入現價／評分"; self.details[item.id]="需有最新現價與因子評分後，才能提供目標價、停損價與判斷。"
            self.table.insert("","end",tags=(self.DECISION_TAGS.get(decision,"neutral"),),values=(item.id,item.symbol,item.name,"—" if price is None else f"{price:.2f}",f"{item.reference_price:.2f}",f"{item.target_price:.2f}",f"{item.stop_price:.2f}",score,decision))
        for child in self.chart_area.winfo_children(): child.destroy()
        rows=[(item.symbol,{c.factor:c.raw_score for c in self.scores[item.symbol][0].contributions}) for item in list_items(self.database) if item.symbol in self.scores]
        if rows: factor_heatmap(self.chart_area,rows,FACTOR_LABELS)
    def show_detail(self,_):
        selected=self.table.selection()
        if not selected:return
        item_id=int(self.table.item(selected[0])["values"][0]); self.detail.configure(state="normal"); self.detail.delete("1.0","end"); self.detail.insert("1.0",self.details[item_id]); self.detail.configure(state="disabled")
    def hover(self,event):
        row=self.table.identify_row(event.y); col=self.table.identify_column(event.x)
        if not row or col != "#9": self.hide_tip(); return
        self.hide_tip(); self.tip_job=self.after(1000,lambda:self.show_tip(row,event.x_root,event.y_root))
    def show_tip(self,row,x,y):
        # An override-redirect window is never repositioned by the window
        # manager to stay on-screen -- confirmed real: hovering the
        # rightmost/lowest rows (e.g. "判斷" column, the last column) placed
        # part of the tooltip past the screen edge, silently clipping the
        # text with no visual indication anything was cut off. Flip to the
        # other side of the cursor when the default placement would overflow.
        item_id=int(self.table.item(row)["values"][0])
        self.tooltip=tk.Toplevel(self); self.tooltip.wm_overrideredirect(True)
        label=ttk.Label(self.tooltip,text=self.details[item_id],background="#ffffe0",padding=6,wraplength=420); label.pack()
        self.tooltip.update_idletasks()
        tip_width,tip_height=self.tooltip.winfo_reqwidth(),self.tooltip.winfo_reqheight()
        screen_width,screen_height=self.tooltip.winfo_screenwidth(),self.tooltip.winfo_screenheight()
        pos_x=x+12 if x+12+tip_width<=screen_width else max(0,x-12-tip_width)
        pos_y=y+12 if y+12+tip_height<=screen_height else max(0,y-12-tip_height)
        self.tooltip.wm_geometry(f"+{pos_x}+{pos_y}")
    def hide_tip(self):
        if self.tip_job:self.after_cancel(self.tip_job); self.tip_job=None
        if self.tooltip:self.tooltip.destroy(); self.tooltip=None
