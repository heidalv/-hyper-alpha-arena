# -*- coding: utf-8 -*-
"""[H134 2026-09-21] 查 Aster 交易所实际支持哪些合约（找 USD1 合约 / 低 taker 市场）。

# 为什么关键

官方费率文档（`docs.asterdex.com/trading/perpetuals/fees-and-specs/fees.md`）：

    USDT-Perpetual   Maker 0%     Taker **0.04%**  = 4.0bp   ← 我们现在用的
    USD1 General     Maker 0%     Taker **0.005%** = 0.5bp   ← 便宜 8 倍
    RWA Perpetual    Maker 0%     Taker 0.009%     = 0.9bp
    付费用 $ASTER                   再省 5%

而我们的成本结构实测（H105/H126）：强平腿每周期成本 ≈ **10.67bp**，
其中 taker 费 4.36bp（含滑点口径）占 **41%**。
⇒ 若能把 taker 从 4.0bp 降到 0.5bp，每周期成本可从 10.67bp 降到 ≈ 7.2bp（**−32%**）。

# 本脚本做什么

打 Aster 公开 API，列出所有永续合约的 symbol + 状态，按后缀分类：
  · `USDT` 结尾  → USDT 永续（4.0bp）
  · `USD1` 结尾  → USD1 General 永续（**0.5bp**）★
  · 其它         → 归类

并检查我们关心的币（ASTER/XRP/SOL/ZEC/BNB/HYPE）分别有没有 USD1 版本。

用法：
    .venv\\Scripts\\python.exe scripts\\h134_aster_usd1_markets.py
"""
from __future__ import annotations

import json
from collections import defaultdict

import requests

BASES = [
    "https://fapi.asterdex.com",
    "https://asterdex.com",
    "https://api.asterdex.com",
]
PATHS = [
    "/fapi/v1/exchangeInfo",
    "/api/v1/exchangeInfo",
    "/fapi/v3/exchangeInfo",
]
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"}

WANT = ["ASTER", "XRP", "SOL", "ZEC", "BNB", "HYPE", "DOGE", "BTC", "ETH"]


def main() -> int:
    info = None
    for b in BASES:
        for p in PATHS:
            u = b + p
            try:
                r = requests.get(u, headers=H, timeout=20)
                if r.status_code == 200:
                    j = r.json()
                    n = len(j.get("symbols") or [])
                    print(f"  命中 {u}  status=200  symbols={n}")
                    if n:
                        info = j
                        break
                else:
                    print(f"  {u}  status={r.status_code}")
            except Exception as e:
                print(f"  {u}  FAIL {type(e).__name__}")
        if info:
            break

    if not info:
        print("\n  所有端点都没拿到 exchangeInfo ⇒ 需要换端点（检查 API 域名）")
        return 1

    syms = info.get("symbols") or []
    by_suffix = defaultdict(list)
    for s in syms:
        name = str(s.get("symbol") or "")
        status = str(s.get("status") or s.get("contractStatus") or "")
        # 后缀分类
        for suf in ("USD1", "USDT", "BUSD", "USDC"):
            if name.endswith(suf):
                by_suffix[suf].append((name, status))
                break
        else:
            by_suffix["其它"].append((name, status))

    print("\n" + "=" * 96)
    print("H134  Aster 合约分类")
    print("=" * 96)
    for suf, lst in sorted(by_suffix.items(), key=lambda kv: -len(kv[1])):
        live = [n for n, st in lst if st.upper() in ("TRADING", "1", "ONLINE", "")]
        print(f"\n  [{suf}] 共 {len(lst)} 个（其中可交易 {len(live)}）")
        print(f"    {', '.join(sorted(n for n, _ in lst)[:40])}")

    print("\n" + "=" * 96)
    print("我们关心的币有没有 USD1 版本（0.5bp taker）")
    print("=" * 96)
    usd1 = {n for n, _ in by_suffix.get("USD1", [])}
    print(f"  {'币':<8} {'USDT 版':>10} {'USD1 版':>10}   可用低价市场？")
    print("  " + "-" * 52)
    for w in WANT:
        a = f"{w}USDT" in {n for n, _ in by_suffix.get("USDT", [])}
        b = f"{w}USD1" in usd1
        print(f"  {w:<8} {str(a):>10} {str(b):>10}   {'**是**' if b else '否'}")

    print("\n  全部 USD1 合约：")
    print("   ", ", ".join(sorted(usd1)) or "(无)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
