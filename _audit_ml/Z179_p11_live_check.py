# -*- coding: utf-8 -*-
"""Z179（P11 运行期核验）：图审闸的时效参数在运行进程里的实际值 + 一个真实案例复算。

用 DB 里**最新一条图审信号**（真实年龄）跑一次 chart_gate_check，展示：
  * 通用上限（240min）与立场 TTL（180min）的实际生效值；
  * 该信号若带 no_new_long，在当前年龄下是"否决"还是"因陈旧放行"。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from backend.services.full_auto import midlong_chart_gate as g  # noqa: E402

print("=== 时效参数（运行期）===")
print("  MIDLONG_CHART_MAX_SIGNAL_AGE_MIN =", os.getenv("MIDLONG_CHART_MAX_SIGNAL_AGE_MIN", "(未设 → 240)"))
print("  MIDLONG_CHART_ADVICE_TTL_MIN     =", os.getenv("MIDLONG_CHART_ADVICE_TTL_MIN", "(未设 → 180)"))
print("  _advice_ttl_min() =", g._advice_ttl_min())

print("\n=== 各币最新图审信号年龄 + 复算判据 ===")
try:
    from backend.services.analysis import ledgers

    for sym in ("BTC", "ETH", "SOL"):
        sig = g._latest_chart_signal(sym)
        if not sig:
            print(f"  {sym}: 无新鲜信号（→ 按 required/fail-open 规则）")
            continue
        age = int(max(0, (g.time.time() * 1000 - int(sig.get("created_ms") or 0)) / 60000))
        advice = (sig.get("payload") or {}).get("position_advice") if isinstance(sig.get("payload"), dict) else None
        ok_long, reason_long, _ = g.chart_gate_check(sym, "buy", tier="mid")
        print(f"  {sym}: age={age}min advice={advice!r} → buy: {'放行' if ok_long else '否决'}  {reason_long[:110]}")
except Exception as exc:  # noqa: BLE001
    print("  查询失败:", type(exc).__name__, str(exc)[:160])
