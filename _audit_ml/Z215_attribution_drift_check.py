# -*- coding: utf-8 -*-
"""Z215：归因漏记是**正在发生**还是**历史/状态重置**造成的？

判据（决定性）：取最近 N 笔已平仓 mid/long（按 closed_at 倒序），逐笔检查：
  1. 该 position_id 是否在归因 `tags` 里（开仓时打过标）；
  2. 其 `tier|通道` 是否出现在 `breaker` 里，且 `n` 是否包含它；
  3. 对比"最近 24h/7d 的通道计数"与 DB 实际笔数 —— 若近期一致 ⇒ 无持续漏记；
     若近期仍差很多 ⇒ 持续漏记（需要修调用点）。
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

state = json.loads(Path(sa._STATE_PATH).read_text(encoding="utf-8"))
tags = state.get("tags") or {}
br = state.get("breaker") or {}
print(f"归因状态: tags={len(tags)} breaker={len(br)} ts={state.get('ts')}")

NET = ("((p.close_price - p.entry_price) * p.size * "
       "case when lower(p.side) in ('long','buy') then 1 else -1 end"
       " + coalesce(p.partial_realized_pnl,0) - coalesce(p.partial_fee_paid,0))")

db = SessionLocal()
db.execute(t("set app.is_admin='on'"))
try:
    print("\n=== 1. 最近 12 笔已平仓 mid/long：是否被打标 + 通道是否入账 ===")
    rows = db.execute(t("""
        select p.id, p.symbol, p.timeframe_tier, p.closed_at,
               coalesce(split_part(coalesce(p.close_reason,''),':',1),'') ch, round((%s)::numeric,2) net
        from paper_positions p
        where p.status='closed' and p.close_price is not null
          and p.timeframe_tier in ('mid','long')
        order by p.closed_at desc limit 12
    """ % NET)).fetchall()
    tagged = 0
    for r in rows:
        pid = str(int(r[0]))
        is_tagged = pid in tags
        tagged += 1 if is_tagged else 0
        key = f"{r[2]}|{sa.normalize_reason(r[4])}"
        e = br.get(key) or {}
        rec = e.get("recent")
        print(f"  #{pid} {str(r[1]):6s} {str(r[2]):5s} {str(r[3])[:19]} ch={str(r[4])[:22]:24s} "
              f"净={float(r[5] or 0):>8.2f} 打标={'✅' if is_tagged else '❌'} "
              f"breaker.n={e.get('n','—')} recent={len(rec) if isinstance(rec,list) else '—'}")
    print(f"  ⇒ 最近 12 笔中已打标 {tagged}/12")

    print("\n=== 2. 近期（24h / 7d）通道计数对比：DB 实际 vs breaker.n 增量 ===")
    for label, cond in (("24h", "now() - interval '1 day'"), ("7d", "now() - interval '7 days'")):
        rows = db.execute(t(f"""
            select coalesce(split_part(coalesce(p.close_reason,''),':',1),'') ch,
                   p.timeframe_tier tier, count(*) n
            from paper_positions p
            where p.status='closed' and p.close_price is not null and p.closed_at > {cond}
            group by 1,2 order by 3 desc limit 8
        """)).fetchall()
        print(f"  ── {label} ──")
        for ch, tier, n in rows:
            key = f"{tier}|{sa.normalize_reason(ch)}"
            e = br.get(key) or {}
            rec = e.get("recent")
            rl = len(rec) if isinstance(rec, list) else 0
            print(f"    {str(ch)[:26]:28s} {str(tier):5s} DB={n:4d} 窗口样本={rl:3d} 累计n={e.get('n','—')}")
finally:
    db.rollback()
    db.close()
