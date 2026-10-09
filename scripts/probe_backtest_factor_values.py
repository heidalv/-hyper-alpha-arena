# -*- coding: utf-8 -*-
"""[F349 诊断] 为什么回测里的因子值取不到？逐层打印。

判定链：compute_all_factors(klines_df) → 因子值 dict
  可能断点：① FACTOR_LIVE_ALLOWLIST_ONLY 收敛后与引擎 FACTORS 无交集（fail-closed 返回空）
            ② 窗口 K 线列名/长度不满足
            ③ 异常被吞
"""
from __future__ import annotations

import io
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s",
                    stream=sys.stdout)

from backend.services.strategy_evolver import StrategyEvolver  # noqa: E402

ev = StrategyEvolver()
bars = ev._load_bars("BTC", "4h", 30)
print(f"bars={len(bars)}")

import pandas as pd  # noqa: E402
from backend.services.factor_engine import factor_engine  # noqa: E402

i = len(bars) - 5
wb = bars[max(0, i - 29):i + 1]
df = pd.DataFrame([{'open': b.o, 'high': b.h, 'low': b.l, 'close': b.c,
                    'volume': b.v, 'timestamp': b.timestamp} for b in wb])
print(f"窗口={len(wb)} 列={list(df.columns)}")

print("\n--- ① 直接调用 compute_all_factors(allowlist=None) ---")
fv = factor_engine.compute_all_factors(df)
print(f"返回类型={type(fv).__name__} 条目={len(fv) if fv else 0}")
if fv:
    ks = list(fv)[:8]
    print("样例键:", ks)

print("\n--- ② 显式传 allowlist=set() 看是否 fail-closed 变空 ---")
try:
    fv2 = factor_engine.compute_all_factors(df, allowlist=set())
    print(f"allowlist=set() → 条目={len(fv2) if fv2 else 0}")
except TypeError as e:
    print("签名不支持 allowlist:", e)

print("\n--- ③ 引擎自报的受治理名单（与 FACTORS 的交集） ---")
try:
    from backend.services.factor_engine.active_set_policy import (
        ActiveSetRole, load_factor_active_rows)
    from backend.services.factor_engine.key_utils import normalize_engine_key
    rows = load_factor_active_rows(ActiveSetRole.TRADABLE, parse_expr=False)
    ids = {normalize_engine_key(str(r.get("factor_id") or "")) for r in rows
           if not str(r.get("source") or "").startswith("seed_bootstrap")}
    ids.discard("")
    print(f"TRADABLE 行={len(rows)} → 归一化 id={len(ids)}")
    fac = getattr(factor_engine, "FACTORS", {}) or {}
    print(f"引擎注册因子数={len(fac)}")
    inter = ids & set(fac)
    print(f"交集={len(inter)} {sorted(inter)[:10]}")
except Exception as e:
    print("读取失败:", str(e)[:300])

print("\n--- ④ ENV ---")
for k in ("FACTOR_LIVE_ALLOWLIST_ONLY", "BACKTEST_FACTOR_ATTR", "FACTOR_COMBO_MODE"):
    print(f"  {k}={os.getenv(k)}")
