# -*- coding: utf-8 -*-
"""[审计] 用新的 gate_decision 读当前线上的 flow_gate_last.json —— 看会不会开门。"""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from backend.services.market_maker.flow_rules import gate_decision  # noqa: E402

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\data\flow_gate_last.json")
doc = json.loads(p.read_text(encoding="utf-8"))
print("线上门文件字段:", list((list((doc.get("gates") or {}).values()) or [{}])[0].keys()))
print("文件里有没有 oos 段:", any("oos" in (v or {}) for v in (doc.get("gates") or {}).values()))
print("文件里有没有 mu  :", any((v or {}).get("mu") is not None
                            for v in (doc.get("gates") or {}).values()))
for sym in list((doc.get("gates") or {}).keys())[:3]:
    g = gate_decision(doc, sym, time.time())
    print(f"  gate_decision({sym}) ⇒ allow={g['allow']} reason={g['reason']}")
# 如果按新格式补上 oos 会怎样
doc2 = json.loads(p.read_text(encoding="utf-8"))
for k, v in (doc2.get("gates") or {}).items():
    v["oos"] = {"mean_y": 0.5, "n_eff": 40, "win_rate": 0.55}
    v["mu"] = 1.5
sym0 = list(doc2["gates"].keys())[0]
g2 = gate_decision(doc2, sym0, time.time())
print(f"  补上 oos 后 gate_decision({sym0}) ⇒ allow={g2['allow']} reason={g2['reason']} "
      f"side={g2['side']}")
