# -*- coding: utf-8 -*-
"""Z124: `chart_gate_veto` 真实构成（当前 TOP2 拦截，占近 5 天 27.1%）+ 图审信号可用性。

关注点：
  1. 三条规则（position_advice 禁令 / 强反向 / 亏损后再开需同向支持）各占多少；
  2. 图审信号是否真的在产出（signal_ledger 里 dual:trend_chart_review 的新鲜度）；
  3. 失败语义：`无新鲜图审信号（fail-open）` 与 `信号陈旧…fail-open` 是否在审计里出现
     —— 这两条**不会**写 chart_gate_veto，但会决定闸是否形同不存在。
"""
from __future__ import annotations

import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from backend.services.mlto.midlong_direction_audit import _iter_rows  # noqa: E402

RULE = {
    "advice_no_new_long": re.compile(r"position_advice=no_new_long"),
    "advice_no_new_short": re.compile(r"position_advice=no_new_short"),
    "strong_conflict": re.compile(r"图审强反向"),
    "loss_reopen_no_support": re.compile(r"24h内.*亏损全平且图审未给同向支持"),
    "other": re.compile(r".*"),
}
rules = Counter()
per_day = Counter()
per_sym = Counter()
samples = {}
n_veto = 0
for row in _iter_rows():
    reason = str(row.get("reason") or "")
    if "chart_gate" not in reason and "图审" not in reason:
        continue
    n_veto += 1
    for name, rx in RULE.items():
        if rx.search(reason):
            rules[name] += 1
            samples.setdefault(name, reason[:150])
            break
    ts = float(row.get("epoch") or 0)
    if ts:
        per_day[datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d")] += 1
    per_sym[str(row.get("symbol") or "?")] += 1

print(f"=== chart_gate 相关审计行 {n_veto} ===")
for k, v in rules.most_common():
    print(f"   {v:>7}  {v/max(1,n_veto):>6.1%}  {k}")
    if k in samples:
        print(f"            样本: {samples[k]}")
print("\n按日（近 20 天）:", dict(sorted(per_day.items())[-20:]))
print("按标的:", dict(per_sym.most_common(10)))

# 图审信号可用性
print("\n=== 图审信号（signal_ledger, dual:trend_chart_review）===")
try:
    from backend.services.analysis import ledgers
    syms = ["BTC", "ETH", "SOL", "BNB", "XRP", "UNI", "VIRTUAL", "XPL", "ASTER"]
    now_ms = time.time() * 1000
    fresh = 0
    for s in syms:
        rows = ledgers.list_signals(source="dual:trend_chart_review", symbol=s, limit=5) or []
        ages = []
        for r in rows:
            try:
                ages.append(round((now_ms - int(r.get("created_ms") or 0)) / 60000))
            except Exception:
                pass
        ages = [a for a in ages if a >= 0]
        newest = min(ages) if ages else None
        if newest is not None and newest <= 240:
            fresh += 1
        print(f"   {s:<9} n={len(rows):<3} 最新信号年龄(min)={newest}")
    print(f"   ⇒ 有 ≤4h 新鲜信号的标的 {fresh}/{len(syms)}")
except Exception as e:
    print("   图审信号查询失败:", str(e)[:160])
