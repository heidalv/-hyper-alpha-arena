# -*- coding: utf-8 -*-
"""重算"缺陷饲料占载荷"比例（§38.3），把 F387（reflexion_memory）计入。只读。

口径（必须写明，纪律 23）：分子 = 已证有缺陷的键的**紧凑 JSON**字符数之和；
分母 = extras 全部键的**紧凑 JSON**字符数之和。与 §38.3 的历史读数同口径。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OLD = ("similar_episodes", "recent_pnl_14d", "backtest_wisdom",
       "recent_same_dir_pnl_14d", "consolidated_lessons")
NEW = OLD + ("reflexion_memory",)

from backend.services.mlto import brain as B  # noqa: E402

sid = "probe"
try:
    from sqlalchemy import text
    from backend.database.connection import SessionLocal
    db = SessionLocal()
    db.execute(text("SET app.is_admin='on'"))
    row = db.execute(text("SELECT session_id FROM full_auto_sessions "
                          "ORDER BY id DESC LIMIT 1")).fetchone()
    db.close()
    if row:
        sid = str(row[0])
except Exception as exc:  # noqa: BLE001
    print(f"[warn] {exc}")

for sym in ("BTC", "ETH"):
    feed = B.build_feed(symbol=sym, tier="mid", session_id=sid, market_summary=None)
    extra = feed.get("extras") or {}
    total = len(json.dumps(extra, ensure_ascii=False, default=str))
    def s(keys):
        return sum(len(json.dumps(extra.get(k), ensure_ascii=False, default=str))
                   for k in keys if k in extra)
    print(f"\n[{sym}/mid] extras 合计 = {total} 字符")
    print(f"  旧口径（5 键，§38.3）: {s(OLD)}/{total} = {s(OLD)/total:.1%}")
    print(f"  新口径（+reflexion_memory）: {s(NEW)}/{total} = {s(NEW)/total:.1%}")
    print(f"  reflexion_memory 本身 = {s(('reflexion_memory',))} 字符")
