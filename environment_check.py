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

    missing_packages = []

    for pkg_name, module_name in packages.items():
        try:
            module = __import__(module_name)
            version = getattr(module, '__version__', '未知版本')
            print(f"{pkg_name}：{version}")
        except ImportError:
            print(f"❌ {pkg_name}：未安裝")
            missing_packages.append(pkg_name)

    print(f"SQLite：{sqlite3.sqlite_version}")
    print("=" * 60)

    if missing_packages:
        print("\n⚠️ 發現缺少的必要套件，準備自動為您安裝...")
        print(f"即將安裝: {', '.join(missing_packages)}")
        print("這可能需要一到兩分鐘，請稍候...\n")

        import subprocess
        try:
            # 呼叫系統指令自動安裝
            subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing_packages)
            print("\n✅ 所有缺少套件已自動安裝完成！")
            print("請重新執行程式 (例如再次輸入 python main.py) 讓新套件生效。")
            # 安裝完成後建議中斷程式，讓 Python 重新啟動以正確載入剛安裝的模組
            sys.exit(0)
        except subprocess.CalledProcessError as e:
            print(f"\n❌ 自動安裝失敗 (錯誤碼 {e.returncode})。")
            print(f"請手動在終端機輸入：python -m pip install {' '.join(missing_packages)}")
            sys.exit(1)
    else:
        print("✅ 環境檢查完成，所有必要套件均可正常載入。")

if __name__ == "__main__":
    check_environment()
