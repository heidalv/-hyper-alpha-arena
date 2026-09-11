# -*- coding: utf-8 -*-
"""Z208：通道熔断（`_channel_shadowed`）对当前泄漏通道是否**本来就会**生效？以及是否被接线。

两问：
  1. 用熔断器自己的口径（close_reason×tier 近 30 笔 wr<40%）算：`thesis_should_close` /
     `thesis_invalidation` / `trend_broken` 各自是否满足 shadow 条件？
  2. 论题硬离场路径（`resolve_thesis_hard_exit` → sentinel）有没有调用该熔断？
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "backend"))

from sqlalchemy import text as t  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

NET = ("((p.close_price - p.entry_price) * p.size * "
       "case when lower(p.side) in ('long','buy') then 1 else -1 end"
       " + coalesce(p.partial_realized_pnl,0) - coalesce(p.partial_fee_paid,0))")

print("=== 1. 熔断器实现（阈值/口径）===")
try:
    from backend.services.source_attribution import attribution
    src = inspect.getsource(type(attribution).exit_channel_shadow)
    for line in src.splitlines():
        s = line.strip()
        if any(k in s for k in ("def ", "30", "0.4", "wr", "win", "return", "shadow")):
            print("   ", s[:120])
except Exception as exc:  # noqa: BLE001
    print("   读取失败:", exc)

print("\n=== 2. 各通道近 30 笔胜率（熔断器口径）===")
db = SessionLocal()
db.execute(t("set app.is_admin='on'"))
try:
    rows = db.execute(t(f"""
        with r as (
          select split_part(coalesce(p.close_reason,''),':',1) kind, p.timeframe_tier tier,
                 {NET} net,
                 row_number() over (partition by split_part(coalesce(p.close_reason,''),':',1), p.timeframe_tier
                                    order by p.closed_at desc) rn
          from paper_positions p
          where p.status='closed' and p.close_price is not null
            and p.timeframe_tier in ('mid','long','short')
        )
        select kind, tier, count(*) n,
               sum(case when net>0 then 1 else 0 end) wins,
               round(sum(net)::numeric,2) netsum
        from r where rn <= 30 group by 1,2 having count(*) >= 3
        order by 1,2
    """)).fetchall()
    print(f"  {'通道':34s} {'tier':6s} {'近30笔':>6s} {'胜率':>6s} {'净额':>9s}  熔断条件(<40%)")
    for r in rows:
        wr = r[3] / r[2] if r[2] else 0
        flag = "⇒ 应 shadow" if wr < 0.40 else ""
        print(f"  {str(r[0])[:34]:34s} {str(r[1]):6s} {r[2]:>6d} {wr:>6.0%} {float(r[4] or 0):>9.2f}  {flag}")

    print("\n=== 3. 论题硬离场路径是否接线到熔断器 ===")
    pm = (ROOT / "backend/services/full_auto/midlong_position_manager.py").read_text(encoding="utf-8", errors="replace")
    # 找 resolve_thesis_hard_exit 函数体范围
    m = re.search(r"def resolve_thesis_hard_exit\(.*?\n(?=def |\Z)", pm, re.S)
    body = m.group(0) if m else ""
    print(f"  resolve_thesis_hard_exit 内出现 _channel_shadowed: {'✅' if '_channel_shadowed' in body else '❌ 否'}")
    callers = [ln for ln in pm.splitlines() if "resolve_thesis_hard_exit(" in ln and "def " not in ln]
    print(f"  调用点数: {len(callers)}")
    for c in callers[:5]:
        print("    ", c.strip()[:100])
    loop = (ROOT / "backend/services/full_auto/loops/midlong_loop.py").read_text(encoding="utf-8", errors="replace")
    hits = [ln.strip() for ln in loop.splitlines() if "thesis" in ln.lower() and ("exit" in ln.lower() or "close" in ln.lower())]
    print(f"  midlong_loop 中论题离场相关行 {len(hits)} 条（前 6）:")
    for h in hits[:6]:
        print("    ", h[:110])
finally:
    db.rollback()
    db.close()
