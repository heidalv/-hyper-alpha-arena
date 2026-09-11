# -*- coding: utf-8 -*-
"""Z66: 用「同分钟同标的」关联法反推 1866 条通用拒仓的真实原因（09-08~09-09）。"""
from __future__ import annotations
import json, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
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
            ts = datetime.fromtimestamp(float(r["epoch"]), timezone.utc)
            gen.append((ts.strftime("%Y-%m-%d %H:%M"), str(r.get("symbol")), str(r.get("tier"))))
print("通用拒仓行数:", len(gen), " 时间范围:", gen[0][0], "→", gen[-1][0])
need = {(m, s) for m, s, _ in gen}
print("需要关联的 (分钟,标的) 桶:", len(need))

MARKERS = [
    ("V5Gate_BLOCK", r"\[V5Gate\] BLOCK"),
    ("Persistence", r"\[Persistence\]"),
    ("Agent无策略", r"\[Agent独立\].*无 active 策略"),
    ("BudgetService满", r"\[BudgetService\]"),
    ("TrancheGate", r"\[TrancheGate\]"),
    ("DecisionPriceGate", r"\[DecisionPriceGate\]"),
    ("LiveDust", r"\[LiveDust\]"),
    ("TradeGate拒", r"TradeGate rejected order"),
    ("SlotGate", r"\[SlotGate\]|\[slot"),
    ("MidLongPortfolio", r"\[MidLongPortfolio\]"),
    ("portfolio_block", r"midlong_portfolio_block"),
    ("ChokeGate", r"\[MidLongChokeGate\]"),
    ("open_ready", r"\[MidLong\] stage=open_ready"),
    ("PB_OPEN", r"\[PaperEngine\]|开仓成功|place_order"),
    ("TierCircuit", r"\[TierCircuit\]"),
    ("open_blocked事件", r"open_blocked"),
    ("execute_false", r"execute_false"),
    ("MidLongExec拒", r"\[MidLongExecutor\]"),
    ("paper_trade_false", r"\[FullAuto\].*(开仓|下单).*(失败|未成功|拒绝)"),
    ("scalp_gate", r"scalp_new_open_blocked|\[ScalpOpenGate\]"),
    ("SubPosition", r"\[SubPosition\]|子仓"),
    ("PersistenceTicks", r"MIDLONG_PERSISTENCE"),
]
rx_ts = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
rx_sym = re.compile(r"symbol=([A-Z0-9]+)")
rx_sym2 = re.compile(r"\]\s*([A-Z0-9]{2,10})\b")

hits = defaultdict(Counter)   # (minute,sym) -> Counter(marker)
logs = [p for p in (ROOT / "logs").glob("*.log") if p.stat().st_size > 1_000_000]
logs = sorted(logs, key=lambda p: -p.stat().st_size)[:8]
print("扫描日志:", [(p.name, round(p.stat().st_size / 1e6)) for p in logs])
for p in logs:
    try:
        f = p.open(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    with f:
        for line in f:
            tsm = rx_ts.match(line)
            if not tsm:
                continue
            minute = tsm.group(1)
            if minute < "2026-09-08 08:00" or minute > "2026-09-09 18:00":
                continue
            m = rx_sym.search(line) or rx_sym2.search(line)
            if not m:
                continue
            sym = m.group(1)
            key = (minute, sym)
            if key not in need:
                continue
            for name, rx in MARKERS:
                if re.search(rx, line):
                    hits[key][name] += 1

matched = sum(1 for k in hits if hits[k])
print(f"\n可关联到日志证据的通用拒仓桶: {matched}/{len(need)}")
agg = Counter()
for k, c in hits.items():
    for name in c:
        agg[name] += 1
print("\n命中的标记分布（按桶去重）:")
for name, n in agg.most_common():
    print(f"  {n:>5}  {name}")

print("\n示例（前 10 个桶的命中详情）:")
for k in sorted(hits)[:10]:
    print(f"   {k}: {dict(hits[k])}")
unmatched = [k for k in need if not hits[k]]
print(f"\n完全无日志证据的桶: {len(unmatched)}，示例: {unmatched[:8]}")
