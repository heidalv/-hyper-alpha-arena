# -*- coding: utf-8 -*-
"""Z214：`record_close` 是否覆盖了全部平仓路径？（熔断窗预热的根因）

判据：状态文件里 `breaker` 的累计计数 `n` 与该通道**实际平仓笔数**（近 30 天 DB）对比。
若某通道实际平了很多笔但 `n` 很小 ⇒ `record_close` 没被那些路径调用（窗口永不预热）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from sqlalchemy import text as t  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services import source_attribution as sa  # noqa: E402

br = (json.loads(Path(sa._STATE_PATH).read_text(encoding="utf-8")).get("breaker") or {})
db = SessionLocal()
db.execute(t("set app.is_admin='on'"))
try:
    rows = db.execute(t("""
        select split_part(coalesce(close_reason,''),':',1) ch, timeframe_tier tier, count(*) n
        from paper_positions
        where status='closed' and close_price is not null and closed_at > now() - interval '30 days'
        group by 1,2 order by 3 desc limit 20
    """)).fetchall()
    print(f"{'通道':30s} {'tier':6s} {'实际平仓':>8s} {'breaker.n':>10s} {'recent':>7s}  判定")
    for ch, tier, n in rows:
        key = f"{tier}|{sa.normalize_reason(ch)}"
        e = br.get(key) or {}
        bn = e.get("n")
        rec = e.get("recent")
        rl = len(rec) if isinstance(rec, list) else "—"
        verdict = ""
        if bn is None:
            verdict = "❌ 从未进入归因（record_close 未调用）"
        elif isinstance(bn, int) and bn < n:
            verdict = f"⚠ 计数偏少（差 {n - bn}）"
        print(f"{ch[:30]:30s} {tier:6s} {n:>8d} {str(bn):>10s} {str(rl):>7s}  {verdict}")
finally:
    db.rollback()
    db.close()
