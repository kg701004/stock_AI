# Stock AI - 第一階段：資料抓取與儲存

這是一套基於 Python 撰寫的自動化股票歷史資料抓取工具。
它能自動化幫您從 Yahoo Finance 抓取台、美、日、韓股市的歷史資料（開高低收、成交量），並儲存到本地的 SQLite 資料庫中。它具備「智慧增量更新」機制，每天執行只會補足最新的資料，大幅減少下載時間並避免被封鎖。

## 準備工作 (Windows 環境)

1. **建立資料夾**:
   請在您的 D 槽建立一個資料夾，路徑為 `D:\Stock_AI`

2. **複製程式碼**:
   請將這包所有的程式碼檔案（包含 `main.py`, `environment_check.py`, `src/` 等）複製並放入 `D:\Stock_AI` 裡面。

3. **開啟終端機/命令提示字元**:
   打開您的 VS Code 並開啟 `D:\Stock_AI` 資料夾。開啟終端機 (Terminal)。

4. **安裝必要套件** (如果您還沒安裝):
   ```bash
   python -m pip install pandas numpy requests yfinance
   ```

## 執行方式

在 VS Code 的終端機中，執行以下指令即可啟動程式：

### 1. 一般更新 (推薦)
程式會自動檢查哪些股票還缺資料，並補足到今天。
```bash
python main.py
```

### 2. 強制重新下載所有資料
如果您覺得資料庫有損壞，想從「上市第一天」重新抓取所有股票資料，請加上 `--force` 參數 (請注意這會花費較長的時間)：
```bash
python main.py --force
```

### 3. 只更新特定市場
如果您今天只想更新台股和美股，可以使用 `--markets` 參數：
```bash
python main.py --markets TW,US
```

### 4. 略過環境檢查
如果您確定環境都安裝好了，想要加快啟動速度：
```bash
python main.py --skip-env
```

## 資料庫位置
抓取完畢的資料會存放在 `D:\Stock_AI\database\stock_data.db`。您可以使用任何支援 SQLite 的軟體 (例如 DB Browser for SQLite) 打開並查看裡面的內容。
