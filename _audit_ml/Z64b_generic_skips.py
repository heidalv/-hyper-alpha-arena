# -*- coding: utf-8 -*-
"""Z64b: 1866 条通用拒仓的时空分布 + 与日志的对照线索。"""
from __future__ import annotations
import json, os, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = ROOT / "data" / "midlong_direction_audit.jsonl"
rows = []
with p.open(encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass

gen = [r for r in rows if str(r.get("reason")) == "evaluate_and_execute_returned_false"]
ok = [r for r in rows if str(r.get("reason")) != "evaluate_and_execute_returned_false"]
print("通用拒仓行:", len(gen), " 其它行:", len(ok))
print("首行时间:", datetime.fromtimestamp(float(rows[0]['epoch']), timezone.utc).isoformat() if rows else "-")
print("末行时间:", datetime.fromtimestamp(float(rows[-1]['epoch']), timezone.utc).isoformat() if rows else "-")

def hh(mm):
    return Counter(datetime.fromtimestamp(float(r["epoch"]), timezone.utc).strftime("%m-%d %H:%M")[:11] for r in mm)
print("\n[通用拒仓] 按 UTC 日:", dict(hh(gen)))
print("[其它拒仓] 按 UTC 日:", dict(hh(ok)))
print("\n[通用拒仓] 按小时(UTC 日+时):")
for k, v in sorted(hh(gen).items()):
    print(f"   {k}  {v}")
print("\n[通用拒仓] 按 symbol:", dict(Counter(str(r.get("symbol")) for r in gen).most_common(12)))
print("[通用拒仓] 按 tier:", dict(Counter(str(r.get("tier")) for r in gen)))
print("[通用拒仓] 按 action:", dict(Counter(str(r.get("action")) for r in gen)))
print("[通用拒仓] 按 session:", dict(Counter(str(r.get("session_id")) for r in gen).most_common(6)))
print("\n[通用拒仓] 按 (symbol,action) 组合:", dict(Counter(f"{r.get('symbol')}/{r.get('action')}" for r in gen).most_common(12)))
print("\n对照：同期 opened 行数:", len([r for r in ok if str(r.get("outcome"))=="opened"]))
