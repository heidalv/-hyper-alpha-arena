# -*- coding: utf-8 -*-
"""[新目标 R1] 因子管道 45s 预算的每标的耗时构成探针（只读，不改任何代码）。

怀疑点：v3_factor_pipeline 循环内每标的三个外部注入：
  inject_orderflow_for_factors / onchain_collector.collect_all / get_options_for_symbol。
本探针逐个计时，找"4-5 个标的就吃掉 45s"的真凶。
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")


def _t(label, fn):
    t0 = time.time()
    try:
        r = fn()
        dt = time.time() - t0
        size = ""
        if isinstance(r, dict):
            size = f" keys={len(r)}"
        print(f"  {label:42s} {dt*1000:8.0f} ms{size}")
        return r, dt
    except Exception as e:  # noqa: BLE001
        dt = time.time() - t0
        print(f"  {label:42s} {dt*1000:8.0f} ms  ERR {type(e).__name__}: {str(e)[:60]}")
        return None, dt


for sym in ("BTC", "DOGE"):
    print(f"=== {sym} ===")
    _, d1 = _t("inject_orderflow_for_factors(5m)", lambda: __import__(
        "backend.services.factor_engine.factor_bridge", fromlist=["inject_orderflow_for_factors"]
    ).inject_orderflow_for_factors(sym, {}, "5m"))
    _, d2 = _t("onchain_collector.collect_all([sym])", lambda: __import__(
        "backend.services.onchain_data_collector", fromlist=["onchain_collector"]
    ).onchain_collector.collect_all([sym]))
    _, d3 = _t("get_options_for_symbol", lambda: __import__(
        "backend.services.options_data_collector", fromlist=["get_options_for_symbol"]
    ).get_options_for_symbol(sym))
    print(f"  合计 ≈ { (d1 + d2 + d3) * 1000:.0f} ms/标的\n")
print("参考：45s 预算 ÷ 该合计 = 每轮可跑标的数")
