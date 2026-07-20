import sqlite3
import pandas as pd
import os
from typing import List, Optional

class DatabaseManager:
    def __init__(self, db_path: str = "database/stock_data.db"):
        self.db_path = db_path
        self._create_tables()

    def get_connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _create_tables(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 建立股票基本資料表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT UNIQUE NOT NULL,
                    market TEXT NOT NULL,
                    name TEXT,
                    is_active BOOLEAN DEFAULT 1
                )
            ''')

            # 建立日K線資料表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS daily_candles (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume INTEGER,
                    PRIMARY KEY (symbol, date)
                )
            ''')

            # 建立更新紀錄表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS update_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    last_update_date TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(symbol)
                )
            ''')

            conn.commit()

    def add_symbol(self, symbol: str, market: str, name: str = ""):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO symbols (symbol, market, name)
                VALUES (?, ?, ?)
            ''', (symbol, market, name))
            conn.commit()

    def get_all_symbols(self, market: Optional[str] = None) -> List[str]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if market:
                cursor.execute('SELECT symbol FROM symbols WHERE market = ? AND is_active = 1', (market,))
            else:
                cursor.execute('SELECT symbol FROM symbols WHERE is_active = 1')
            return [row[0] for row in cursor.fetchall()]

    def save_daily_data(self, symbol: str, df: pd.DataFrame):
        if df.empty:
            return

        # 確保 DataFrame 的欄位名稱符合資料庫設計
        # 預期 df 的 index 是日期 (DatetimeIndex)
        df_to_save = df.copy()
        df_to_save = df_to_save.reset_index()
        df_to_save['symbol'] = symbol

        # 處理日期格式 (Y-M-D)
        if 'Date' in df_to_save.columns:
            df_to_save['date'] = df_to_save['Date'].dt.strftime('%Y-%m-%d')
        elif 'Datetime' in df_to_save.columns:
             df_to_save['date'] = df_to_save['Datetime'].dt.strftime('%Y-%m-%d')
        elif 'index' in df_to_save.columns:
             df_to_save['date'] = df_to_save['index'].dt.strftime('%Y-%m-%d')

        # 選取需要的欄位並重新命名以符合資料庫
        col_mapping = {
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        }
        df_to_save = df_to_save.rename(columns=col_mapping)

        cols_to_keep = ['symbol', 'date', 'open', 'high', 'low', 'close', 'volume']
        # 確保欄位存在
        for col in cols_to_keep:
            if col not in df_to_save.columns:
                 df_to_save[col] = None

        df_to_save = df_to_save[cols_to_keep]

        with self.get_connection() as conn:
            # 使用 executemany 來插入或取代資料
            records = df_to_save.to_records(index=False).tolist()
            cursor = conn.cursor()
            cursor.executemany('''
                INSERT OR REPLACE INTO daily_candles (symbol, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', records)

            # 更新 update_logs
            latest_date = df_to_save['date'].max()
            cursor.execute('''
                INSERT OR REPLACE INTO update_logs (symbol, last_update_date, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (symbol, latest_date))

            conn.commit()

    def get_last_update_date(self, symbol: str) -> Optional[str]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT last_update_date FROM update_logs WHERE symbol = ?
            ''', (symbol,))
            result = cursor.fetchone()
            return result[0] if result else None

    def load_daily_data(self, symbol: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        query = "SELECT * FROM daily_candles WHERE symbol = ?"
        params = [symbol]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date ASC"

        with self.get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=params)
            if not df.empty:
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
            return df
