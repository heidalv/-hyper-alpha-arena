# -*- coding: utf-8 -*-
"""[§90 核查 2026-09-11 / 范围外但同账户] `research` 层 30 天 **−$413.08** 的画像（只读）。

发现路径：查"逐层 30 天净额"时看到 ——
```
research | closed | 112 笔 | -413.08
short    | closed |1562 笔 | -174.10
long     | closed |  34 笔 | -143.57
mid      | closed | 156 笔 |  -73.36
```
即 **research 层是账户里 30 天最大的单一亏损层**（比 mid+long 合计 −$216.93 还多 90%），
而它不在本轮"中长线"口径内 ⇒ 必须显式量化并交回用户判断（是否属预期实验成本/是否有配额）。

本脚本回答：它是什么（谁开的、什么 nature/strategy）、亏在哪（通道/peak 分桶）、
以及它是否受配额/风控约束（读 daily_quota 配置与 tier 相关键）。
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from backend.database.connection import SessionLocal  # noqa: E402
from backend.services.exit.channel_breaker_gate import channel_of  # noqa: E402
from sqlalchemy import text  # noqa: E402

NET = ("(case when lower(side) in ('long','buy') then (close_price-entry_price)*size "
       "else -(close_price-entry_price)*size end) "
       "+ coalesce(partial_realized_pnl,0) - coalesce(partial_fee_paid,0)")


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        rows = db.execute(text(f"""
            select upper(symbol), coalesce(trade_nature,'?'), coalesce(strategy_id,'-'),
                   coalesce(close_reason,'?'), coalesce(peak_pnl_pct,0),
                   {NET} as net, coalesce(leverage,0), coalesce(size,0), entry_price, account_id,
                   case when opened_at is null then null
                        else extract(epoch from (closed_at-opened_at))/3600.0 end
            from paper_positions
            where status='closed' and closed_at >= now() - interval '{days} day'
              and lower(coalesce(timeframe_tier,'')) = 'research'
        """)).fetchall()
        open_now = db.execute(text("""
            select count(*) from paper_positions where status='open'
              and lower(coalesce(timeframe_tier,''))='research'
        """)).scalar()
    finally:
        db.close()

    n = len(rows)
    net = sum(float(r[5] or 0) for r in rows)
    wins = sum(1 for r in rows if float(r[5] or 0) > 0)
    peaks = [float(r[4] or 0) for r in rows]
    lo = sum(1 for p in peaks if p < 0.02)
    holds = [float(r[10]) for r in rows if r[10] is not None]
    notionals = [float(r[7] or 0) * float(r[8] or 0) for r in rows]

    print("=" * 100)
    print(f"research 层画像（近 {days} 天，n={n}；当前仍开着 {open_now} 个）")
    print("=" * 100)
    print(f"  净额 {net:+.2f}｜胜率 {100.0*wins/max(1,n):.1f}%｜peak<2% 占比 {100.0*lo/max(1,n):.1f}%"
          f"｜平均持有 {(sum(holds)/max(1,len(holds))):.1f}h"
          f"｜平均名义 ${(sum(notionals)/max(1,len(notionals))):.0f}"
          f"｜账户 {sorted({int(r[9]) for r in rows})}")
    print(f"\n  通道分布（按金额）：")
    ch = {}
    for r in rows:
        k = channel_of(r[3])
        a = ch.setdefault(k, [0, 0.0])
        a[0] += 1
        a[1] += float(r[5] or 0)
    for k, (c, v) in sorted(ch.items(), key=lambda kv: kv[1][1])[:8]:
        print(f"    {k:26s} {c:>3} 笔 {v:>10.2f}")
    print("\n  标的 TOP6：", Counter(r[0] for r in rows).most_common(6))
    print("  nature：", Counter(r[1] for r in rows).most_common(4))
    print("  strategy 前缀 TOP6：", Counter((r[2] or '-')[:24] for r in rows).most_common(6))
    print("\n  peak 分桶（看是否同 mid/long：主漏点仍是『从未走出来』）：")
    for name, lo_, hi_ in (("<0.5%", -9, 0.005), ("0.5–1%", 0.005, 0.01), ("1–2%", 0.01, 0.02),
                           ("2–5%", 0.02, 0.05), ("≥5%", 0.05, 9)):
        sel = [float(r[5] or 0) for r in rows if lo_ <= float(r[4] or 0) < hi_]
        if sel:
            print(f"    {name:8s} n={len(sel):>3} 净额 {sum(sel):>9.2f}")

    # 配额/风控是否覆盖 research 层
    print("\n  配额与风控键（是否覆盖 research 层）：")
    for key in ("MIDLONG_DAILY_QUOTA", "DAILY_QUOTA_TOTAL", "DAILY_QUOTA_RESEARCH",
                "PAPER_DAILY_QUOTA", "POSITION_CONSTRUCTION_ENFORCE",
                "MIDLONG_MAX_OPEN_POSITIONS", "MIDLONG_MAX_NET_EXPOSURE_PCT"):
        v = os.environ.get(key)
        if v is not None:
            print(f"    {key} = {v}")
    print("    （未出现的键 ⇒ 该层未单独设配额；见 daily_quota 模块口径）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
