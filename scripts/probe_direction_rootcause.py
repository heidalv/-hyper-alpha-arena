# -*- coding: utf-8 -*-
"""根因取证：主脑方向（short）到底是谁定的。只读。

数据源：Analytics 库 `ai_decision_logs.decision_snapshot`（persist_hub_decision 写入），
字段：direction / dir_src / hub_action / hub_adjusted / llm_qual / fw_mean / regime / hub_mode。
"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter, defaultdict
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

    print("=" * 96)
    print("A. 表结构（确认 decision_snapshot 类型）")
    for c, t in db.execute(text("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_name='ai_decision_logs' ORDER BY ordinal_position
    """)).fetchall():
        print(f"    {c:28s} {t}")

    # 探测列口径：snapshot 是 jsonb 还是 text
    row = db.execute(text("""
        SELECT decision_snapshot FROM ai_decision_logs
        WHERE decision_snapshot IS NOT NULL ORDER BY id DESC LIMIT 1
    """)).fetchone()
    kind = type(row[0]).__name__ if row else "?"
    print(f"\n    decision_snapshot Python 类型 = {kind}")

    print("\n" + "=" * 96)
    print("B. 近 6 小时 hub 决策：direction × dir_src 分布")
    rows = db.execute(text("""
        SELECT decision_snapshot, created_at FROM ai_decision_logs
        WHERE created_at >= now() - interval '6 hours'
          AND decision_snapshot IS NOT NULL
        ORDER BY id DESC LIMIT 4000
    """)).fetchall()
    print(f"   取到 {len(rows)} 条")
    if not rows:
        rows = db.execute(text("""
            SELECT decision_snapshot, created_at FROM ai_decision_logs
            WHERE decision_snapshot IS NOT NULL ORDER BY id DESC LIMIT 4000
        """)).fetchall()
        print(f"   （近 6h 无，退回最近 {len(rows)} 条）")

    dir_cnt = Counter()
    src_cnt = Counter()
    pair = Counter()
    llmq = defaultdict(list)
    fwm = defaultdict(list)
    mode_cnt = Counter()
    ts_range = []
    for snap, ts in rows:
        try:
            d = snap if isinstance(snap, dict) else json.loads(snap or "{}")
        except Exception:  # noqa: BLE001
            continue
        if not d.get("_hub_decision_log"):
            continue
        dr = str(d.get("direction") or "")
        sr = str(d.get("dir_src") or "")
        dir_cnt[dr] += 1
        src_cnt[sr] += 1
        pair[(dr, sr)] += 1
        mode_cnt[str(d.get("hub_mode") or "")] += 1
        if isinstance(d.get("llm_qual"), (int, float)):
            llmq[sr].append(float(d["llm_qual"]))
        if isinstance(d.get("fw_mean"), (int, float)):
            fwm[sr].append(float(d["fw_mean"]))
        if ts:
            ts_range.append(str(ts)[:19])

    print(f"   时间跨度: {min(ts_range) if ts_range else '?'} → {max(ts_range) if ts_range else '?'}")
    print(f"   hub_mode 分布: {dict(mode_cnt)}")
    print(f"\n   direction 分布: {dict(dir_cnt)}")
    print(f"   dir_src   分布: {dict(src_cnt)}")
    print("\n   (direction, dir_src) 组合 Top10:")
    for (d_, s_), n in pair.most_common(10):
        print(f"      {n:5d}  direction={d_:8s} dir_src={s_}")

    print("\n   按 dir_src 看 llm_qual / fw_mean（均值与范围）:")
    for s_ in sorted(llmq, key=lambda k: -len(llmq[k])):
        v = llmq[s_]
        w = fwm.get(s_, [])
        print(f"      {s_:12s} n={len(v):5d}  llm_qual均值={sum(v)/len(v):.3f} "
              f"[{min(v):.3f},{max(v):.3f}]   "
              f"fw_mean均值={(sum(w)/len(w) if w else float('nan')):.3f}")
finally:
    db.close()
