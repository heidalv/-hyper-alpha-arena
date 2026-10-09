# -*- coding: utf-8 -*-
"""连续锤 tier-activity 端点，捕获 500 的响应体。"""
import time

import requests

url = "http://127.0.0.1:8000/api/full-auto/tier-activity/fa_7e12e7a1b6"
fails = []
for i in range(20):
    try:
        r = requests.get(url, timeout=45)
        if r.status_code != 200:
            fails.append((i, r.status_code, r.text[:700]))
            print(f"[{i}] status={r.status_code}")
            print(r.text[:700])
            print("---")
        else:
            print(f"[{i}] 200 ({len(r.content)}B)")
    except Exception as e:
        print(f"[{i}] EXC {type(e).__name__}: {str(e)[:120]}")
    time.sleep(0.8)

print(f"\n失败次数: {len(fails)}/20")
