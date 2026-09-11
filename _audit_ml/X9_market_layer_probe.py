# -*- coding: utf-8 -*-
"""主脑 market 层 1h 字段线上验证（第十八轮）：真实数据构建 BTC/ETH market 层。"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from backend.services.analysis.context_pack import build_market_layer  # noqa: E402

errors = []
out = build_market_layer(["BTC", "ETH"], errors)
print("errors:", errors)
for sym, d in (out.get("symbols") or {}).items():
    keys_1h = {k: d.get(k) for k in (
        "ret_1h_pct", "ret_24h_pct", "pos24_pct", "range_24h_high", "range_24h_low",
        "rsi14_1h", "atr14_1h_pct", "ema_trend_1h")}
    print(f"\n[{sym}] 1h 派生字段：")
    print(json.dumps(keys_1h, ensure_ascii=False, indent=2))
    print(f"  1d/4h 对照： ret_1d_pct={d.get('ret_1d_pct')} rsi14_1d={d.get('rsi14_1d')} "
          f"ema_trend_4h={d.get('ema_trend_4h')} regime={d.get('regime')}")
