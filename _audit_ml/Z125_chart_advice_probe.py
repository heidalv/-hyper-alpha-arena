# -*- coding: utf-8 -*-
"""Z125: 图审信号 payload 实证 —— `position_advice` 的取值分布与来源（是否存在默认值兜底）。

关键问题：98% 的图审否决来自单条规则 `position_advice=no_new_long`。若该字段在某处被
**默认写成 no_new_long**（或缺失时按 no_new_long 处理），就会变成"一个字段关掉整条中线车道"
的静默失效；反之若它是图审 LLM 的真实输出，则属策略层判断（需用户决策）。
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from backend.services.analysis import ledgers  # noqa: E402

SYMS = ["BTC", "ETH", "SOL", "BNB", "XRP", "UNI", "VIRTUAL", "XPL", "ASTER"]
adv = Counter()
now_ms = time.time() * 1000
print("=== signal_ledger（dual:trend_chart_review）最新 5 条/标的的 payload ===")
for s in SYMS:
    rows = ledgers.list_signals(source="dual:trend_chart_review", symbol=s, limit=5) or []
    for r in rows:
        pl = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        a = str(pl.get("position_advice") or "<空>").lower()
        adv[a] += 1
        age = round((now_ms - int(r.get("created_ms") or 0)) / 60000)
        if age <= 600:  # 只打印较新的
            print(f"  {s:<9} age={age:>4}min dir={r.get('direction')} s={r.get('strength')} "
                  f"advice={a:<14} keys={sorted(pl)[:6]}")
print("\n=== position_advice 取值分布（9 标的 × 最近 5 条）===")
for k, v in adv.most_common():
    print(f"   {v:>4}  {k}")

# 是否存在"缺字段 → 默认 no_new_long"的逻辑
print("\n=== 代码里 position_advice 的产生与消费 ===")
import re  # noqa: E402
pat = re.compile(r"position_advice")
for p in (ROOT / "backend").rglob("*.py"):
    if ".venv" in str(p) or "tests" in p.parts:
        continue
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for i, line in enumerate(txt.splitlines(), 1):
        if pat.search(line):
            print(f"   {p.relative_to(ROOT)}:{i}  {line.strip()[:130]}")
