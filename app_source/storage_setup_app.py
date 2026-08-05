"""First-run storage-folder picker for the future standalone desktop app."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import ui_theme
from storage_paths import configure_storage, default_storage_root


class StorageSetupApp(ttk.Frame):
    """Let the user choose a data root and create a safe standard layout."""

    def __init__(self, master: tk.Misc, on_complete: callable | None = None) -> None:
        super().__init__(master, padding=18)
        self.master.title("Stock AI 資料位置設定")
        self.grid(sticky="nsew")
        self.on_complete = on_complete
        self.location = tk.StringVar(value=str(default_storage_root()))
        ttk.Label(self, text="請選擇股票資料儲存位置：").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Entry(self, textvariable=self.location, width=58).grid(row=1, column=0, pady=8, sticky="ew")
        ttk.Button(self, text="選擇資料夾", command=self.choose_folder).grid(row=1, column=1, padx=(8, 0))
        ttk.Label(self, text="系統將自動建立 history.sqlite、decision_audit.sqlite、raw_archive、imports 與 backups。", wraplength=500).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Button(self, text="儲存並建立資料夾", style="Primary.TButton", command=self.save).grid(row=3, column=0, columnspan=2, pady=(16, 0))
        self.columnconfigure(0, weight=1)

    def choose_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.location.get() or None, title="選擇 Stock AI 資料資料夾")
        if selected:
            self.location.set(selected)

    def save(self) -> None:
        try:
            configure_storage(Path(self.location.get()))
            messagebox.showinfo("完成", f"資料位置已設定為：\n{self.location.get()}")
            if self.on_complete:
                self.on_complete()
            else:
                self.master.destroy()
        except (OSError, ValueError) as error:
            messagebox.showerror("無法設定", str(error))


def main() -> None:
    root = tk.Tk()
    ui_theme.apply(root)
    StorageSetupApp(root)
    root.minsize(560, 200)
    root.mainloop()


if __name__ == "__main__":
    main()
