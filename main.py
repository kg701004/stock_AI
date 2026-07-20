import os
import sys
import logging
import argparse

from environment_check import check_environment

def setup_directories():
    """建立專案所需的所有資料夾"""
    dirs = ['data', 'database', 'reports', 'logs',
            'src/providers', 'src/indicators', 'src/strategies',
            'src/backtest', 'src/models', 'src/ui']
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    print("✅ 資料夾架構建立完成。")

def setup_logger():
    """設定記錄檔儲存"""
    os.makedirs('logs', exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("logs/system.log", encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

def main():
    parser = argparse.ArgumentParser(description="股市分析系統 - 自動更新工具")
    parser.add_argument('--force', action='store_true', help="強制重新下載所有歷史資料 (預設為增量更新)")
    parser.add_argument('--markets', type=str, default='US,TW,JP,KR', help="要更新的市場 (逗號分隔，如 US,TW)")
    parser.add_argument('--skip-env', action='store_true', help="跳過環境檢查")

    args = parser.parse_args()

    # 1. 檢查環境與建立資料夾
    if not args.skip_env:
        check_environment()

    # 在環境檢查完畢後才載入系統核心模組，避免遇到 ModuleNotFoundError 而崩潰
    from src.database.db_manager import DatabaseManager
    from src.providers.yfinance_provider import YFinanceProvider
    from src.update_manager import UpdateManager

    setup_directories()
    setup_logger()

    logger = logging.getLogger(__name__)
    logger.info("="*60)
    logger.info("啟動股市分析系統核心服務")
    logger.info("="*60)

    # 2. 啟動資料庫與 Provider
    db = DatabaseManager("database/stock_data.db")
    # 設定 delay = 1.0 秒避免被 Yahoo Finance 封鎖
    provider = YFinanceProvider(delay_seconds=1.0)

    updater = UpdateManager(db, provider)

    # 3. 解析市場清單並初始化股票
    markets = [m.strip().upper() for m in args.markets.split(',') if m.strip()]
    logger.info(f"將處理以下市場: {markets}")
    updater.initialize_markets(markets)

    # 4. 開始自動更新
    logger.info(f"更新模式: {'強制完整下載' if args.force else '智慧增量更新'}")
    updater.update_all(markets=markets, force_full=args.force)

    logger.info("="*60)
    logger.info("所有作業已完成，您可以關閉視窗。")
    logger.info("="*60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️ 使用者強制中斷程式。")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 程式發生嚴重錯誤: {e}")
        sys.exit(1)
