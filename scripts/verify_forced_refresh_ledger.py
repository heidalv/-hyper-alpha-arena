# -*- coding: utf-8 -*-
"""核实「强制重析」在决策台账里的记录（只读）。"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import AnalyticsSessionLocal  # noqa: E402

db = AnalyticsSessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    print("=== 近 40 分钟的 _brain_decision_log（含两次强制重析）===")
    rows = db.execute(text("""
        SELECT created_at, symbol, decision_snapshot FROM ai_decision_logs
        WHERE decision_snapshot LIKE '%_brain_decision_log%'
          AND created_at >= now() - interval '40 minutes'
        ORDER BY id DESC LIMIT 8
    """)).fetchall()
    for ts, sym, snap in rows:
        try:
            d = json.loads(snap)
        except Exception:  # noqa: BLE001
            d = {}
        print(f"  {ts} {str(sym):10s} dir={d.get('direction')!s:8s} "
              f"src={d.get('dir_src')!s:10s} llm_qual={d.get('llm_qual')} "
              f"conv={d.get('llm_conviction')} accepted={d.get('accepted')} "
              f"rec_open={d.get('recommend_open')}")

    print("\n=== 近 40 分钟方向分布 ===")
    rows2 = db.execute(text("""
        SELECT decision_snapshot FROM ai_decision_logs
        WHERE decision_snapshot LIKE '%_brain_decision_log%'
          AND created_at >= now() - interval '40 minutes'
    """)).fetchall()
    c = Counter()
    for (snap,) in rows2:
        try:
            d = json.loads(snap)
        except Exception:  # noqa: BLE001
            continue
        c[str(d.get("direction"))] += 1
    tot = sum(c.values())
    print(f"   {dict(c)}  合计 {tot}")
    if tot:
        print(f"   short 占比 = {c.get('short', 0) / tot:.1%}")
    if tot < 20:
        print("   ⚠️ 样本 < 20 ⇒ 不足以判定（仅作方向性观察）")
finally:
    db.close()
