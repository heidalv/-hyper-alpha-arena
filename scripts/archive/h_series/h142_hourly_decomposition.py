# -*- coding: utf-8 -*-
"""H142：按小时拆解「价差收入 vs 行情损失」，判断改动的**剩余瓶颈**在哪。"""
from __future__ import annotations

from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    print("=" * 92)
    print("H142  按小时拆解：价差收入 vs 行情损失 vs 手续费")
    print("=" * 92)
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT date_trunc('hour', ts) AS h, count(*) AS n,
                       sum(spread_bp*notional/1e4) AS sp,
                       sum(price_bp*notional/1e4)  AS pr,
                       sum(fee_bp*notional/1e4)    AS fe,
                       sum(net_bp*notional/1e4)    AS net,
                       sum(notional)               AS notl
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND event='fill'
                  AND ts >= '2026-09-21 08:00:00+08'
                GROUP BY 1 ORDER BY 1
            """)
            rows = cur.fetchall()

    print(f"\n  {'小时':<7} {'笔数':>6} {'名义$':>11} {'价差+$':>9} {'行情-$':>9} "
          f"{'费-$':>8} {'净$':>9}   {'价差bp':>7} {'行情bp':>8}")
    print("  " + "-" * 88)
    for (h, n, sp, pr, fe, net, notl) in rows:
        sp, pr, fe, net, notl = (float(x or 0) for x in (sp, pr, fe, net, notl))
        b = 1e4 / notl if notl else 0.0
        print(f"  {h.strftime('%H:%M'):<7} {n:>6} {notl:>11,.0f} {sp:>+9.3f} {pr:>+9.3f} "
              f"{fe:>+8.3f} {net:>+9.3f}   {sp*b:>+7.3f} {pr*b:>+8.3f}")

    print("\n  读法：")
    print("    价差+ 是我们赚的（挂单捕获），行情- 是我们赔的（被逆向选择），费- 是 taker。")
    print("    ⇒ 若「行情-」量级与「价差+」相当 ⇒ 瓶颈是**逆向选择**（挂单总在被穿时成交），")
    print("      那要动的是**挂单宽度/深度**，而不是出库方式。")
    print("    ⇒ 若「费-」仍是大头 ⇒ 还有 taker 在跑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
