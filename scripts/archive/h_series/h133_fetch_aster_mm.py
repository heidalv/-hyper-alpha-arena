# -*- coding: utf-8 -*-
"""抓 Aster 做市商计划 + 手续费档位（找 taker 0.5bp 的 USD1 合约能不能用）。"""
from __future__ import annotations

import re

import requests

URLS = [
    "https://docs.asterdex.com/trading/perpetuals/market-maker-program.md",
    "https://docs.asterdex.com/trading/perpetuals/fees-and-specs.md",
    "https://docs.asterdex.com/trading/perpetuals.md",
    "https://docs.asterdex.com/llms.txt",
]
H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "text/markdown,text/plain,*/*",
}


def clean(t: str) -> str:
    if "<html" in t.lower():
        t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
        t = re.sub(r"(?is)<br\s*/?>", "\n", t)
        t = re.sub(r"(?is)</(p|div|tr|h[1-6]|li)>", "\n", t)
        t = re.sub(r"(?s)<[^>]+>", " ", t)
        t = t.replace("&nbsp;", " ").replace("&amp;", "&")
        t = re.sub(r"[ \t]{2,}", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
    return t


for u in URLS:
    print("=" * 100)
    print("URL:", u)
    try:
        r = requests.get(u, headers=H, timeout=30)
        print(f"  status={r.status_code} len={len(r.text)}")
        if r.status_code != 200:
            continue
        t = clean(r.text)
        # 只打印与费率/档位/做市相关的片段
        keys = ("maker", "taker", "Maker", "Taker", "rebate", "Rebate", "fee", "Fee",
                "tier", "Tier", "volume", "Volume", "0.04", "0.005", "0.009",
                "USD1", "market maker", "Market Maker", "MM1", "MM2", "MM3")
        lines = [l.strip() for l in t.splitlines()]
        hit = [l for l in lines if l and any(k in l for k in keys)]
        print(f"  相关行 {len(hit)} 条（前 70 条）：")
        for l in hit[:70]:
            print("   ", l[:190])
    except Exception as e:
        print(f"  FAIL {type(e).__name__}: {e}")
