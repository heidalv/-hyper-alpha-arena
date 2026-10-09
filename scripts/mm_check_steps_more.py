# -*- coding: utf-8 -*-
"""[F291 2026-09-16] 候选宽价差标的的 $30 腿合规性实测（Aster exchangeInfo 直连）。

F290 普查显示 ADA/UNI/AVAX/LINK 的中位价差是 ETH 的 70~130 倍（2.8~5.2bp vs 0.04bp），
但它们在 `core.SYMBOL_STEP` 表外 ⇒ 必须用官方 exchangeInfo 实测 stepSize/minQty/
minNotional，判断 "$30 单腿能否下单"（BTC 就是被这一条挡住的：0.001 BTC ≈ $75.8）。
价格取数据库最近盘口 mid（同一数据源，避免引入第二处不一致）。
"""
from __future__ import annotations

import io
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from backend.core.tenant import system_identity  # noqa: E402
from backend.database.connection import MarketSessionLocal  # noqa: E402

SYMS = ["ADAUSDT", "UNIUSDT", "AVAXUSDT", "LINKUSDT", "DOGEUSDT", "SOLUSDT", "ETHUSDT"]
URL = os.getenv("ASTER_FAPI", "https://fapi.asterdex.com") + "/fapi/v1/exchangeInfo"
LEG = float(os.getenv("F291_LEG", "30"))


def _mids(syms):
    since = int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000)
    out = {}
    with system_identity():
        with MarketSessionLocal() as db:
            for s in syms:
                r = db.execute(text(
                    "SELECT (best_bid+best_ask)/2 AS mid FROM market_orderbook_snapshots"
                    " WHERE exchange='asterdex' AND symbol=:s AND timestamp >= :t"
                    "   AND best_bid>0 AND best_ask>best_bid"
                    " ORDER BY timestamp DESC LIMIT 1"), {"s": s.replace("USDT", ""),
                                                          "t": since}).mappings().first()
                if r:
                    out[s] = float(r["mid"])
    return out


def main() -> int:
    mids = _mids(SYMS)
    try:
        raw = urllib.request.urlopen(URL, timeout=25).read().decode("utf-8", "replace")
        info = json.loads(raw)
    except Exception as e:
        print(f"exchangeInfo 拉取失败: {e}")
        return 1
    by = {s["symbol"]: s for s in info.get("symbols", [])}
    print(f"[F291] exchangeInfo 币对={len(by)} 腿量=${LEG:.0f}")
    print(f"{'symbol':<10}{'step':>12}{'minQty':>10}{'minNotional':>12}{'mid':>12}"
          f"{'腿量Qty':>12}  合规")
    for s in SYMS:
        d = by.get(s)
        if not d:
            print(f"{s:<10}  exchangeInfo 无此币对")
            continue
        f = {x["filterType"]: x for x in d.get("filters", [])}
        lot = f.get("LOT_SIZE", {})
        step, minq = float(lot.get("stepSize", 0) or 0), float(lot.get("minQty", 0) or 0)
        minn = float((f.get("MIN_NOTIONAL", {}) or {}).get("notional", 0) or 0)
        mid = float(mids.get(s) or 0.0)
        if step <= 0 or mid <= 0:
            print(f"{s:<10}{step:>12}{minq:>10}{minn:>12}{mid:>12}  （缺数据）")
            continue
        qty = math.floor((LEG / mid) / step) * step
        ok = qty >= minq - 1e-12 and qty * mid >= minn - 1e-9
        why = "✓ 可下单" if ok else (f"✗ 一步={step*mid:.2f}$" if qty < minq else f"✗ 名义={qty*mid:.2f}$")
        print(f"{s:<10}{step:>12}{minq:>10}{minn:>12}{mid:>12,.4f}{qty:>12}  {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
