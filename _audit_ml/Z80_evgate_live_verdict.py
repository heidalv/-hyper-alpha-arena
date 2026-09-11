# -*- coding: utf-8 -*-
"""Z80: EV 闸影子放行的时间分布 + 用生产函数实测当前裁决（不改状态）。"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

# ── A. 日志时间分布（去重）──
rx_ts = re.compile(r"^(\d{4}-\d{2}-\d{2})")
files = sorted([p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 200_000],
               key=lambda p: -p.stat().st_size)
by_day = Counter()
natures = Counter()
seen = set()
for p in files:
    try:
        f = p.open(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    with f:
        for line in f:
            if "[MidLongEvGate]" not in line:
                continue
            key = line.strip()[:200]
            if key in seen:
                continue
            seen.add(key)
            m = rx_ts.match(line)
            if m:
                by_day[m.group(1)] += 1
            m2 = re.search(r"\[(\w+)\]$", line.strip())
            if m2:
                natures[m2.group(1)] += 1
print("=== EV 闸日志（去重）按日 ===")
for d, n in sorted(by_day.items()):
    print(f"   {d}  {n}")
print("按 nature:", dict(natures))

# ── B. 用生产函数实测当前裁决 ──
print("\n=== 当前裁决（生产函数，真实配置）===")
from backend.services.decision_core.midlong_ev_gate import midlong_ev_gate  # noqa: E402

cases = [
    ("swing", "ETH", 60.0, "long", 0.05, 0.045),
    ("swing", "ETH", 75.0, "long", 0.05, 0.045),
    ("swing", "VIRTUAL", 55.0, "long", 0.06, 0.05),
    ("trend_follow", "BTC", 60.0, "long", 0.10, 0.065),
    ("position", "BTC", 60.0, "long", 0.10, 0.065),
]
for nat, sym, score, dirn, tp, sl in cases:
    d = midlong_ev_gate.evaluate(nature=nat, symbol=sym, score=score, direction=dirn,
                                 tp_pct=tp, sl_pct=sl, notional_usd=2000.0)
    print(f"  {nat:<13} score={score} tp={tp:.1%} sl={sl:.1%} → allowed={d.allowed} "
          f"p={d.p_win}({d.p_win_source}) ev={d.ev_pct:+.4%} shadow_cold={d.breakdown.get('shadow_cold_start')}")
    print(f"       reason: {d.reason}")

print("\n=== 闸内统计（本进程）===")
print("  ", midlong_ev_gate.get_stats())
