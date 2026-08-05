# D:\stock_AI 資料分類

```text
D:\stock_AI\
├─ history.sqlite            # 歷史日線、匯入清單與索引資料庫
├─ decision_audit.sqlite     # 評分、持股建議、自選追蹤的稽核資料
├─ raw_archive\              # 原始資料，依 年\月\SHA-256.csv.gz 封存
├─ imports\                  # 等待匯入的 CSV；匯入成功後可自行移走或保留
└─ backups\                  # 使用 SQLite backup API 建立的日期備份
```

分類原則：

1. **原始資料不覆寫**：保留來源、發布時間與 SHA-256，發生資料修正時以新版本另存。
2. **資料庫依用途分開**：`history.sqlite` 儲存市場歷史；`decision_audit.sqlite` 儲存系統判斷與持股／自選紀錄，避免互相干擾。
3. **壓縮原始檔、索引化查詢資料**：原始 CSV.gz 節省空間；SQLite 提供快速回測與查詢。
4. **備份不覆寫原檔**：以日期命名，例如 `backups\history_20260722.sqlite`。
5. **每月驗證**：執行 `python history_cli.py verify`，檢查原始封存檔是否與 SHA-256 相符。
