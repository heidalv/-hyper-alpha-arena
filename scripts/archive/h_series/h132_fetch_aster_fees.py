# -*- coding: utf-8 -*-
"""抓 Aster 费率文档（web_fetch 被站点拒，改用 requests 带浏览器头）。"""
from __future__ import annotations

import re

import requests

URLS = [
    "https://docs.asterdex.com/trading/perpetuals/fees-and-specs/fees.md",
    "https://docs.asterdex.com/trading/perpetuals/fees-and-specs/fees",
    "https://docs.asterdex.com/trading/perpetuals/market-maker-program.md",
]
H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}

for u in URLS:
    print("=" * 100)
    print("URL:", u)
    try:
        r = requests.get(u, headers=H, timeout=30)
        print(f"  status={r.status_code} len={len(r.text)} ctype={r.headers.get('content-type')}")
        if r.status_code == 200:
            t = r.text
            # .md 直接给纯文本；HTML 抽正文
            if "<html" in t.lower():
                t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
                t = re.sub(r"(?is)<br\s*/?>", "\n", t)
                t = re.sub(r"(?is)</(p|div|tr|h[1-6]|li)>", "\n", t)
                t = re.sub(r"(?s)<[^>]+>", " ", t)
                t = re.sub(r"&nbsp;", " ", t)
                t = re.sub(r"&amp;", "&", t)
                t = re.sub(r"\n{3,}", "\n\n", t)
            print("-" * 100)
            print(t[:6000])
    except Exception as e:
        print(f"  FAIL {type(e).__name__}: {e}")
