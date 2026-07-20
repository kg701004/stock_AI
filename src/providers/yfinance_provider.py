import yfinance as yf
import pandas as pd
from typing import List, Optional
from datetime import datetime, timedelta
import time
import logging

from .base_provider import BaseProvider

# 設定基本的 logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class YFinanceProvider(BaseProvider):
    def __init__(self, delay_seconds: float = 1.0):
        self.delay_seconds = delay_seconds

    def fetch_historical_data(self, symbol: str, start_date: Optional[str] = None) -> pd.DataFrame:
        """
        抓取歷史資料。
        為了避免 Rate Limit，每次抓取後會暫停 delay_seconds 秒。
        """
        logger.info(f"正在抓取 {symbol} 的歷史資料 (起始日: {start_date or '上市至今'})")
        try:
            ticker = yf.Ticker(symbol)

            if start_date:
                # yfinance 抓取的結束時間預設為今天
                df = ticker.history(start=start_date)
            else:
                # 抓取上市至今所有資料
                df = ticker.history(period="max")

            # 加入延遲防封鎖
            if self.delay_seconds > 0:
                time.sleep(self.delay_seconds)

            if df.empty:
                logger.warning(f"⚠️ 找不到 {symbol} 的資料。")
                return pd.DataFrame()

            # 處理欄位與索引
            # yfinance 回傳的 DataFrame 索引是帶有時區的 DatetimeIndex
            df.index = df.index.tz_localize(None) # 移除時區資訊以便存入資料庫
            return df

        except Exception as e:
            logger.error(f"❌ 抓取 {symbol} 時發生錯誤: {e}")
            return pd.DataFrame()

    def fetch_symbols(self, market: str) -> List[str]:
        """
        回傳各市場的代表性股票代碼清單。
        注意：在第一階段，為了避免一次抓取數萬檔股票耗時過長，我們提供各市場市值前幾名的股票作為預設清單。
        未來您可以透過手動修改 `database` 中的 `symbols` 表格來新增更多股票。
        """
        market = market.upper()
        if market == 'US':
            # 預設提供美股科技巨頭與常見股票
            return ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'TSLA', 'NVDA']
        elif market == 'TW':
            # 預設提供台股常見股票 (台股代碼在 yfinance 要加上 .TW 或 .TWO)
            return ['2330.TW', '2317.TW', '2454.TW', '2308.TW', '2881.TW']
        elif market == 'JP':
            # 預設提供日股常見股票 (日股代碼在 yfinance 要加上 .T)
            return ['7203.T', '6758.T', '9984.T']
        elif market == 'KR':
            # 預設提供韓股常見股票 (韓股代碼在 yfinance 要加上 .KS 或 .KQ)
            return ['005930.KS', '000660.KS']
        else:
            logger.warning(f"未知市場 {market}，回傳空清單。")
            return []
