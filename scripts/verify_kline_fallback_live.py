# -*- coding: utf-8 -*-
"""真实集成验证：陈旧标的的 K 线兜底是否真的把它们从"硬缺项"里救回来。只读+少量API调用。"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 陈旧标的（B29 清单）+ 一个健康标的作对照
STALE = ["SUI", "PLAY", "AVAX", "ADA"]
OK = ["BTC", "ASTER"]
TIER = "mid"

from backend.services.agent_deep_context import _api_klines_fallback, _fetch_klines_for_prompt  # noqa: E402
from backend.services.analysis.context_pack import _klines  # noqa: E402
from backend.services.mlto.brain import _kline_ok  # noqa: E402

print("=" * 96)
print("真实集成：DB→API 兜底后，主脑还判不判 `K线:*` 硬缺项")
print("=" * 96)
print(f"{'symbol':10s} {'tf':4s} {'_klines(20)':>12s} {'兜底单独取':>10s}  {'_kline_ok missing':>26s}")
for sym in STALE + OK:
    for tf in ("1h", "4h", "1d"):
        t0 = time.time()
        rows = _fetch_klines_for_prompt(sym, tf, 20)
        api = _api_klines_fallback(sym, tf, 20) if len(rows) < 20 else []
        ok, missing, last = _kline_ok(sym, TIER)
        flag = "" if not missing else "  ← 仍有缺项"
        print(f"{sym:10s} {tf:4s} {len(rows):>12d} {len(api):>10d}  "
              f"{str(missing):>26s}{flag}   ({time.time()-t0:.1f}s)")

print("\n=== 汇总：主脑的硬缺项是否消失 ===")
for sym in STALE + OK:
    ok, missing, last = _kline_ok(sym, TIER)
    print(f"   {sym:10s} hard_ok={ok}  missing={missing}  last_px={last}")
