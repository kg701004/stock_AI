import platform
import sqlite3
import sys

def check_environment() -> None:
    print("=" * 60)
    print("股市分析系統－環境檢查")
    print("=" * 60)

    # 檢查 Python 版本
    print(f"Python 版本：{platform.python_version()}")

    # 檢查 64-bit
    arch = platform.architecture()[0]
    print(f"Python 架構：{arch}")
    if arch != '64bit':
        print("\n⚠️ 警告：強烈建議使用 64 位元版本的 Python，以免處理大量股市資料時發生記憶體不足問題！")

    # 檢查套件
    packages = {
        'pandas': 'pandas',
        'numpy': 'numpy',
        'requests': 'requests',
        'yfinance': 'yfinance',
    }

    all_passed = True
    for pkg_name, module_name in packages.items():
        try:
            module = __import__(module_name)
            version = getattr(module, '__version__', '未知版本')
            print(f"{pkg_name}：{version}")
        except ImportError:
            print(f"❌ {pkg_name}：未安裝 (請執行 pip install {pkg_name})")
            all_passed = False

    print(f"SQLite：{sqlite3.sqlite_version}")

    print("=" * 60)
    if all_passed:
        print("✅ 環境檢查完成，所有必要套件均可正常載入。")
    else:
        print("❌ 環境檢查失敗，請先安裝缺少的套件。")
        sys.exit(1)

if __name__ == "__main__":
    check_environment()
