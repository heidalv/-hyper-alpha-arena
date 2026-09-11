# -*- coding: utf-8 -*-
"""Z56：收口点闸上线后的**线上核验**（第 12 轮）。

确认三件事：
  1. 收口闸已接线（源码 + 运行态配置）；
  2. 近期是否有 scalp/short 订单被误拦（应为 0——闸只对 mid/long 生效）；
  3. 当前 mid/long 持仓数与上限的关系（闸的实际约束点）。
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")

print("=== 1) 接线与配置 ===")
src = (ROOT / "backend" / "services" / "paper_trading_engine.py").read_text(encoding="utf-8")
print(f"  place_order 接入 choke_point_open_allowed: {'是' if 'choke_point_open_allowed' in src else '否'}")
print(f"  MIDLONG_PORTFOLIO_GATE_ENABLED = {getattr(settings, 'MIDLONG_PORTFOLIO_GATE_ENABLED', None)}")
print(f"  MIDLONG_MAX_OPEN_POSITIONS     = {getattr(settings, 'MIDLONG_MAX_OPEN_POSITIONS', None)}")
print(f"  MIDLONG_CORR_CLUSTER_SYMBOLS   = {getattr(settings, 'MIDLONG_CORR_CLUSTER_SYMBOLS', None)}")

print("\n=== 2) 是否有 scalp/short 被误拦（应为 0）===")
log = ROOT / "logs" / "backend-console.log"
hits = []
if log.is_file():
    size = log.stat().st_size
    with log.open("rb") as f:
        f.seek(max(0, size - 2_000_000))
        tail = f.read().decode("utf-8", "ignore").splitlines()
    pat = re.compile(r"MidLongChokeGate\] 拒单")
    for ln in tail:
        if pat.search(ln):
            tier = re.search(r"tier=(\S+)", ln)
            nature = re.search(r"nature=(\S+)", ln)
            t = (tier.group(1) if tier else "?")
            n = (nature.group(1) if nature else "?")
            hits.append((t, n, ln[:140]))
    print(f"  近 2MB 日志内收口闸拒单数 = {len(hits)}")
    bad = [h for h in hits if h[0] in ("short", "None", "?") and h[0] != "mid" and h[0] != "long"]
    print(f"  其中 tier 非 mid/long 的（异常） = {len(bad)}")
    for h in hits[:8]:
        print(f"    {h[2]}")
else:
    print("  无 backend-console.log")

print("\n=== 3) 当前 mid/long 持仓与上限 ===")
eng = create_engine(URL)
with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    r = c.execute(text("""
        select count(*) from paper_positions
        where status='open' and timeframe_tier in ('mid','long')
    """)).scalar()
    r2 = c.execute(text("""
        select count(*) from paper_positions where status='open'
    """)).scalar()
    print(f"  mid/long 在册 = {r}；全库在册 = {r2}；上限 = {getattr(settings,'MIDLONG_MAX_OPEN_POSITIONS',None)}")
    print("  → 收口闸对 mid/long 生效后，第 N+1 笔 mid/long 开仓（任意入口）都会被拒")
    for q in c.execute(text("""
        select id, symbol, timeframe_tier, trade_nature from paper_positions
        where status='open' order by opened_at
    """)).fetchall():
        print(f"    #{q[0]} {q[1]} {q[2]} {q[3]}")
print(f"\n核验时间 {datetime.now().isoformat(timespec='seconds')}")
