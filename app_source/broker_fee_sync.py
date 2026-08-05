"""Verify the public KGI fee reference at startup without trusting scraped values."""
from __future__ import annotations
import json
import ssl
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
import certifi

KGI_REFERENCE_URL="https://event.kgi.com.tw/news/event/STGFET/index.html"
DEFAULT={"fee_rate":0.001425,"minimum_fee":20,"stock_sell_tax":0.003,"day_trade_sell_tax":0.0015,"etf_sell_tax":0.001}
def verify_and_cache(cache:Path, timeout:int=8)->str:
    """Cache only when the official page contains every expected public rate."""
    try:
        request=Request(KGI_REFERENCE_URL,headers={"User-Agent":"StockAI local research"})
        context=ssl.create_default_context(cafile=certifi.where())
        with urlopen(request,timeout=timeout,context=context) as response: text=response.read().decode("utf-8","ignore")
        required=("0.1425%","0.3%","0.15%","0.1%","20")
        if not all(value in text for value in required): return "凱基費率頁格式未完整辨識；保留既有已驗證費率。"
        cache.parent.mkdir(parents=True,exist_ok=True); cache.write_text(json.dumps({"verified_at":datetime.now().astimezone().isoformat(),"source":KGI_REFERENCE_URL,"rates":DEFAULT},ensure_ascii=False),encoding="utf-8")
        return "凱基公開牌告費率已連線確認。"
    except Exception as error:
        return f"凱基費率連線未確認；保留既有費率（{type(error).__name__}）。"
