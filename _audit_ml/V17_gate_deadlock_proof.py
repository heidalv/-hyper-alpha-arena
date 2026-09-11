# -*- coding: utf-8 -*-
"""V17 合成数据证明：mid 层 + daily=chop + agent=ranging 下长边两闸互斥（结构性死锁）。

不依赖真实数据新鲜度：直接给两个闸喂同一组 (pos24, chg24)。
"""
import sys

import pandas as pd

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

import backend.services.kline_data_service as kds  # noqa: E402
from backend.services.full_auto.midlong_location_gate import location_gate_check  # noqa: E402


def make_klines(pos_pct, chg24_pct, n=30):
    """构造 30 根 1h：最近 24 根的高低区间使 close 落在 pos_pct 分位，且 24h 涨跌=chg24_pct。"""
    base = 100.0
    hi, lo = 110.0, 90.0                     # 区间 [90,110]
    last = lo + (hi - lo) * (pos_pct / 100.0)
    prev24 = last / (1 + chg24_pct / 100.0)
    closes = [base] * (n - 25) + list(pd.Series(range(25)).apply(
        lambda i: prev24 + (last - prev24) * i / 24.0))
    rows = []
    for idx, cl in enumerate(closes):
        rows.append({"timestamp": 1_760_000_000 + idx * 3600, "open": cl,
                     "high": max(cl, hi if idx >= n - 24 else cl),
                     "low": min(cl, lo if idx >= n - 24 else cl), "close": cl})
    # 保证区间高低沿来自最近 24 根
    rows[-1]["high"] = max(rows[-1]["high"], hi)
    rows[-24]["low"] = min(rows[-24]["low"], lo)
    return rows


class _FakeKlineService:
    def __init__(self, rows):
        self._rows = rows

    def get_aggregated_klines(self, sym, tf, count=30):
        return list(self._rows)


def patch(monkeypatch_rows):
    kds.kline_service = _FakeKlineService(monkeypatch_rows)


from backend.services.full_auto.midlong_circuit_gate import _long_learned_ok  # noqa: E402

print(f"{'场景':<28} {'多头闸(learned/chop)':<34} {'位置闸(agent=ranging)':<40} 结论")
for idx, (pos, chg) in enumerate(((40.0, +3.0), (70.0, +3.0), (70.0, +1.0), (40.0, -6.0))):
    sym = f"TESTSYM{idx}"
    rows = make_klines(pos, chg)
    patch(rows)
    # 清掉两闸的进程级缓存，确保每个场景独立求值
    try:
        import backend.services.full_auto.midlong_circuit_gate as _cg
        _cg._LONG_FEAT_CACHE.clear()
    except Exception:
        pass
    try:
        import backend.services.full_auto.midlong_location_gate as _lg
        _lg._RANGE_CACHE.clear()
    except Exception:
        pass
    lr_ok, lr_why = _long_learned_ok(sym, "chop")
    hi = max(r["high"] for r in rows[-24:])
    lo = min(r["low"] for r in rows[-24:])
    px = rows[-1]["close"]
    ms = {sym: {"symbol": sym, "price": px, "range_24h_high": hi,
                "range_24h_low": lo, "price_change_24h_pct": chg}}
    lg_ok, lg_why, _ = location_gate_check(
        sym, "buy", tier="mid", regime="ranging", market_summary=ms)
    if lr_ok and not lg_ok:
        verdict = "**互斥：多头闸放行 / 位置闸拒 → 无单可开**"
    elif (not lr_ok) and lg_ok:
        verdict = "**互斥：多头闸拒 / 位置闸放行 → 无单可开**"
    elif lr_ok and lg_ok:
        verdict = "可开"
    else:
        verdict = "两闸皆拒"
    print(f"pos={pos:>5.0f}% chg24={chg:>+5.1f}%      {str(lr_ok):>5} {lr_why[:26]:<26} "
          f"{str(lg_ok):>5} {lg_why[:33]:<33} {verdict}")
