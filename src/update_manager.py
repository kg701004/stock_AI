import logging
from datetime import datetime, timedelta
from typing import List, Optional

from src.database.db_manager import DatabaseManager
from src.providers.yfinance_provider import YFinanceProvider

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class UpdateManager:
    def __init__(self, db_manager: DatabaseManager, provider: YFinanceProvider):
        self.db = db_manager
        self.provider = provider

    def _get_next_start_date(self, last_update_date: str) -> str:
        """
        計算下一次抓取的起始日期 (last_update_date 的隔天)。
        """
        last_date_obj = datetime.strptime(last_update_date, '%Y-%m-%d')
        next_date_obj = last_date_obj + timedelta(days=1)
        return next_date_obj.strftime('%Y-%m-%d')

    def initialize_markets(self, markets: List[str] = ['US', 'TW', 'JP', 'KR']):
        """
        初始化預設市場的股票代碼。
        如果資料庫中沒有該股票，就加入。
        """
        for market in markets:
            symbols = self.provider.fetch_symbols(market)
            for symbol in symbols:
                self.db.add_symbol(symbol=symbol, market=market)
        logger.info("✅ 市場股票清單初始化完成。")

    def update_symbol(self, symbol: str, force_full: bool = False):
        """
        更新單一股票的歷史資料。
        如果 force_full=True，則強制從上市第一天開始抓。
        否則，查詢資料庫中最後更新的日期，只抓取缺失的天數。
        """
        last_update = None if force_full else self.db.get_last_update_date(symbol)

        start_date = None
        if last_update:
            # 檢查最後更新日期是否就是今天
            today_str = datetime.today().strftime('%Y-%m-%d')
            if last_update >= today_str:
                logger.info(f"⏭️ {symbol} 已是最新資料 ({last_update})，跳過更新。")
                return

            start_date = self._get_next_start_date(last_update)
            logger.info(f"🔄 增量更新 {symbol}：從 {start_date} 開始抓取")
        else:
            logger.info(f"⬇️ 完整下載 {symbol}：從上市至今")

        # 抓取資料
        df = self.provider.fetch_historical_data(symbol, start_date=start_date)

        if not df.empty:
            self.db.save_daily_data(symbol, df)
            logger.info(f"✅ {symbol} 更新完成，共新增 {len(df)} 筆紀錄。")
        else:
            logger.info(f"⚠️ {symbol} 沒有新資料。")

    def update_all(self, markets: Optional[List[str]] = None, force_full: bool = False):
        """
        更新所有設定的股票。
        """
        symbols_to_update = []
        if markets:
            for market in markets:
                symbols_to_update.extend(self.db.get_all_symbols(market))
        else:
            symbols_to_update = self.db.get_all_symbols()

        if not symbols_to_update:
            logger.warning("沒有找到任何股票需要更新。")
            return

        logger.info(f"開始更新共 {len(symbols_to_update)} 檔股票...")
        for i, symbol in enumerate(symbols_to_update, 1):
            logger.info(f"[{i}/{len(symbols_to_update)}] 處理中...")
            self.update_symbol(symbol, force_full=force_full)

        logger.info("🎉 所有股票更新作業完成！")
