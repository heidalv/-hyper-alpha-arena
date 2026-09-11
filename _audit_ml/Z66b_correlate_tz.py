# -*- coding: utf-8 -*-
"""Z66b: 修正时区（日志=本地 +08，审计=UTC）后重新关联 1866 条通用拒仓。"""
from __future__ import annotations
import json, re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
audit = ROOT / "data" / "midlong_direction_audit.jsonl"
gen = []
with audit.open(encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if str(r.get("reason")) == "evaluate_and_execute_returned_false":
            utc = datetime.fromtimestamp(float(r["epoch"]), timezone.utc)
            local = utc + timedelta(hours=8)
            gen.append((local.strftime("%Y-%m-%d %H:%M"), str(r.get("symbol")), str(r.get("tier"))))
print("通用拒仓:", len(gen), "本地时间范围:", gen[0][0], "→", gen[-1][0])
need = defaultdict(list)
for m, s, t in gen:
    need[(m, s)].append(t)
print("待关联 (分钟,标的) 桶:", len(need))

MARKERS = [
    ("V5Gate_BLOCK", r"\[V5Gate\] BLOCK"),
    ("V5Gate_PASS", r"\[V5Gate\] PASS"),
    ("Persistence", r"\[Persistence\]"),
    ("Agent无策略", r"\[Agent独立\].*无 active 策略"),
    ("BudgetService", r"\[BudgetService\]"),
    ("TrancheGate", r"\[TrancheGate\]"),
    ("DecisionPriceGate", r"\[DecisionPriceGate\]"),
    ("LiveDust", r"\[LiveDust\]"),
    ("TradeGate拒", r"TradeGate rejected order"),
    ("portfolio_block", r"midlong_portfolio_block"),
    ("ChokeGate拒", r"\[MidLongChokeGate\]"),
    ("open_ready", r"stage=open_ready"),
    ("TierCircuit", r"\[TierCircuit\]"),
    ("execute_false", r"execute_false"),
    ("open_execute_false", r"open_execute_false"),
    ("PERSISTENCE_TICKS", r"\[Persistence\]"),
    ("MidLongExecutor", r"\[MidLongExecutor\]"),
    ("gates_skip", r"\[MidLong\].*跳过"),
    ("paper_engine拒", r"\[Paper\]|\[PaperEngine\]|拒绝开仓|拒单"),
    ("hub_wait", r"hub_wait_or_neutral"),
    ("watch:", r"reason=watch:"),
    ("fuse", r"stage=fuse"),
    ("circuit", r"midlong_circuit"),
    ("thesis_invalid", r"thesis_invalid|论题失效"),
    ("固定币门", r"FixedSymbolGate"),
    ("K线深度门", r"KlineDepth|深度门"),
]
rx_ts = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
rx_sym = re.compile(r"symbol=([A-Z0-9]+)")
rx_sym2 = re.compile(r"[\s\]]([A-Z0-9]{2,10})\s")
hits = defaultdict(Counter)
logs = [ROOT / "logs" / "backend-console.log", ROOT / "logs" / "brain_subprocess.log",
        ROOT / "logs" / "data-center.log"]
for p in logs:
    if not p.exists():
        continue
    with p.open(encoding="utf-8", errors="ignore") as f:
        for line in f:
            m0 = rx_ts.match(line)
            if not m0:
                continue
            minute = m0.group(1)
            if minute < "2026-09-08 16:00" or minute > "2026-09-10 02:00":
                continue
            ms = rx_sym.search(line)
            if not ms:
                continue
            sym = ms.group(1)
            key = (minute, sym)
            if key not in need:
                continue
            for name, rx in MARKERS:
                if re.search(rx, line):
                    hits[key][name] += 1

matched = sum(1 for k in need if hits[k])
print(f"\n可关联到日志证据的桶: {matched}/{len(need)} = {matched/len(need):.1%}")
agg = Counter()
for k in need:
    for name in hits[k]:
        agg[name] += 1
print("\n命中标记（按桶去重）:")
for name, n in agg.most_common():
    print(f"  {n:>5}  {n/len(need):>6.1%}  {name}")
print("\n示例:")
for k in sorted(need)[:6]:
    print(f"   {k} tiers={need[k]}: {dict(hits[k])}")
un = [k for k in need if not hits[k]]
print(f"\n无日志证据桶: {len(un)}  示例: {un[:6]}")
