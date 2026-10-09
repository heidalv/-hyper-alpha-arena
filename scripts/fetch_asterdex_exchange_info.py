# -*- coding: utf-8 -*-
"""[h667 2026-10-01] 拉取 Asterdex V3 exchangeInfo(公开端点,无需鉴权),
落盘 data/asterdex_exchange_info.json;模拟做市按这些真实过滤器成交:
PRICE_FILTER(tickSize)/LOT_SIZE(stepSize,minQty)/MARKET_LOT_SIZE/MIN_NOTIONAL/
PERCENT_PRICE(相对标记价的百分比限价)/MAX_NUM_ORDERS。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "asterdex_exchange_info.json"
BASE = "https://fapi.asterdex.com"


def main() -> int:
    try:
        r = requests.get(f"{BASE}/fapi/v3/exchangeInfo", timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"x exchangeInfo 获取失败: {e}")
        return 1
    symbols = data.get("symbols") or []
    out = {"fetched_at": data.get("serverTime"), "symbols": {}}
    for s in symbols:
        sym = str(s.get("symbol") or "")
        filters = {}
        for f in (s.get("filters") or []):
            ft = str(f.get("filterType") or "")
            if ft == "PRICE_FILTER":
                filters["tick_size"] = float(f.get("tickSize") or 0.0)
                filters["min_price"] = float(f.get("minPrice") or 0.0)
            elif ft == "LOT_SIZE":
                filters["step_size"] = float(f.get("stepSize") or 0.0)
                filters["min_qty"] = float(f.get("minQty") or 0.0)
            elif ft == "MARKET_LOT_SIZE":
                filters["market_min_qty"] = float(f.get("minQty") or 0.0)
            elif ft == "MIN_NOTIONAL":
                filters["min_notional"] = float(f.get("notional") or 0.0)
            elif ft == "PERCENT_PRICE":
                filters["pct_mult_up"] = float(f.get("multiplierUp") or 0.0)
                filters["pct_mult_down"] = float(f.get("multiplierDown") or 0.0)
            elif ft == "MAX_NUM_ORDERS":
                filters["max_num_orders"] = int(f.get("limit") or 0)
        filters["status"] = str(s.get("status") or "")
        out["symbols"][sym] = filters
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ok 符号数={len(out['symbols'])} 落盘 {OUT}")
    for sym in ("BNBUSDT", "UNIUSDT", "ENAUSDT"):
        if sym in out["symbols"]:
            print(f"  {sym}: {out['symbols'][sym]}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
