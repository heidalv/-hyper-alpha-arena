# -*- coding: utf-8 -*-
"""Z153: 列出 paper/live 分叉审计的全部 25 个条件分支（(c) 项收口用）。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
rows = json.loads((ROOT / "data" / "paper_live_divergence.json").read_text(encoding="utf-8"))
for r in sorted(rows, key=lambda x: (x["kind"], x["file"], x["line"])):
    rel = r["file"].split("/services/")[-1]
    print(f"{r['kind']:11s} {rel}:{r['line']:<5d} {r['test'][:100]}")
print("total", len(rows))
