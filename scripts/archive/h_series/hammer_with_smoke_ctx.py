# -*- coding: utf-8 -*-
"""对照实验：复刻冒烟脚本的 DB 操作后锤 tier-activity，抓 500 响应体。"""
import sys

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")
sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena/backend")

# 复刻冒烟脚本在 HTTP 前的全部操作
from backend.database.connection import SessionLocal  # noqa: F401
from backend.services.decision_fusion_arbiter import _pwin_tiers, decide_scalp  # noqa: F401
import pandas as pd
from backend.services.ai_decision_integration import build_factor_guidance_for_prompt

fake_k = pd.DataFrame({
    "open": [100 + i * 0.1 for i in range(60)],
    "high": [101 + i * 0.1 for i in range(60)],
    "low": [99 + i * 0.1 for i in range(60)],
    "close": [100.5 + i * 0.1 for i in range(60)],
    "volume": [1000.0] * 60,
})
text = build_factor_guidance_for_prompt(["BTC"], {"BTC": fake_k}, {"BTC": 100.5})
print("guidance ok, len", len(text))

import time
import requests

url = "http://127.0.0.1:8000/api/full-auto/tier-activity/fa_7e12e7a1b6"
for i in range(8):
    try:
        r = requests.get(url, timeout=45)
        if r.status_code != 200:
            print(f"[{i}] status={r.status_code}")
            print(r.text[:900])
            break
        print(f"[{i}] 200")
    except Exception as e:
        print(f"[{i}] EXC {type(e).__name__}: {str(e)[:150]}")
    time.sleep(1)
