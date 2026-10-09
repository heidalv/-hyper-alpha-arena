# -*- coding: utf-8 -*-
"""[H184 2026-09-21] 三个币的真实价值：ASTER 在赚，SOL/XRP 在赔。

# H183 的发现

按币（本时代）：

    ASTER  651 笔  价差 +8.733  行情 **−2.676**  净 **+5.368**   ← 唯一在赚
    SOL    583 笔  价差 +4.915  行情 **−11.895** 净 **−7.566**
    XRP    537 笔  价差 +3.443  行情 **−11.602** 净 **−8.829**

而 15:00 之后净 −$8.14 **全部来自行情项**（手续费 = 0）。

# 要回答的

  1. 按**每笔**归一化后，三个币的差距有多大？（笔数不同，要看单位效率）
  2. SOL/XRP 的行情项恶化是**本轮参数改动造成**的，还是**这一时段特有**的？
     ⇒ 分时段看三个币各自的行情项
  3. 剔除 SOL/XRP（只留 ASTER 或加一个更优的币）会怎样？
     ——**用历史数据算，不是猜**
  4. 与选币器当时的选择理由对照：H125 选它们是因为"每周期净额为正"

# 判据（事先定死）

  · 若 SOL/XRP 的每笔净额**分时段持续为负** ⇒ 该剔除（不是时段噪声）
  · 若只有本时段为负、前几时段为正 ⇒ 是行情事件，不该据一次事件剔币
  · 同时给出"A 剔除后"的历史净额，量化剔除的收益

用法：
    .venv\\Scripts\\python.exe scripts\\h184_symbol_value.py
"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


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
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                        " WHERE lane_id=%s", (LANE,))
            since = (cur.fetchone() or [None])[0]

            # 按币 × 小时
            cur.execute("""
                SELECT symbol, to_char(date_trunc('hour', ts), 'HH24') AS h,
                       count(*),
                       round(sum(spread_bp*notional/1e4)::numeric,3),
                       round(sum(price_bp*notional/1e4)::numeric,3),
                       round(sum(fee_bp*notional/1e4)::numeric,3),
                       round(sum(net_bp*notional/1e4)::numeric,3),
                       round(sum(spread_bp*notional)::numeric,0)
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s
                GROUP BY 1,2 ORDER BY 1,2
            """, (LANE, since))
            rows = cur.fetchall()

            # 按币合计 + 名义（用于每笔归一）
            cur.execute("""
                SELECT symbol, count(*), round(sum(notional)::numeric,0),
                       round(sum(spread_bp*notional/1e4)::numeric,3),
                       round(sum(price_bp*notional/1e4)::numeric,3),
                       round(sum(fee_bp*notional/1e4)::numeric,3),
                       round(sum(net_bp*notional/1e4)::numeric,3)
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s
                GROUP BY 1 ORDER BY 7
            """, (LANE, since))
            tot = cur.fetchall()

    print("=" * 96)
    print("H184  三个币的真实价值")
    print("=" * 96)

    print(f"\n  ── 合计（本时代）──")
    print(f"  {'币':<8} {'笔':>5} {'名义$':>12} {'价差$':>9} {'行情$':>9} {'费$':>8} "
          f"{'净$':>9} {'净bp/笔':>9} {'行情bp/笔':>10}")
    print("  " + "-" * 86)
    for (s, n, notl, sp, pr, fe, net) in tot:
        n, notl = int(n), float(notl or 0)
        b = 1e4 / notl if notl else 0.0
        print(f"  {s:<8} {n:>5} {notl:>12,.0f} {float(sp):>+9.3f} {float(pr):>+9.3f} "
              f"{float(fe):>+8.3f} {float(net):>+9.3f} {float(net)*b:>+9.4f} "
              f"{float(pr)*b:>+10.4f}")

    print(f"\n  ── 按币 × 小时：净$（看是否分时段持续为负）──")
    byh = {}
    for (s, h, n, sp, pr, fe, net, spn) in rows:
        byh.setdefault(h, {})[s] = (int(n), float(net), float(pr))
    syms = sorted({s for d in byh.values() for s in d})
    print(f"  {'小时':<6} " + " ".join(f"{s:>14}" for s in syms))
    print("  " + "-" * (8 + 15 * len(syms)))
    for h in sorted(byh):
        cells = []
        for s in syms:
            if s in byh[h]:
                n, net, pr = byh[h][s]
                cells.append(f"{net:>+7.3f}({n:>4})")
            else:
                cells.append(f"{'—':>14}")
        print(f"  {h}:00  " + " ".join(f"{c:>14}" for c in cells))
    print(f"  （格式：净$ (笔数)）")

    print(f"\n  ── 分时段：行情bp/笔（正 = 行情帮我们，负 = 行情害我们）──")
    print(f"  {'小时':<6} " + " ".join(f"{s:>12}" for s in syms))
    print("  " + "-" * (8 + 13 * len(syms)))
    for h in sorted(byh):
        cells = []
        for s in syms:
            if s in byh[h] and byh[h][s][0] > 0:
                n, net, pr = byh[h][s]
                cells.append(f"{pr/n*1e4:>+12.2f}" if False else f"{pr:>+12.4f}")
            else:
                cells.append(f"{'—':>12}")
        print(f"  {h}:00  " + " ".join(cells))
    print(f"  （单位为美元；负数 = 该小时该币的行情项在赔）")

    print(f"\n  ── 剔除情景（用历史数据算）──")
    total_net = sum(float(r[6]) for r in tot)
    print(f"    当前三币合计净额 **{total_net:+.4f}**")
    for drop in (["SOL"], ["XRP"], ["SOL", "XRP"]):
        keep = [r for r in tot if r[0] not in drop]
        kept = sum(float(r[6]) for r in keep)
        print(f"    剔除 {','.join(drop):<10} ⇒ 剩余净额 **{kept:+.4f}**"
              f"（{'改善' if kept > total_net else '恶化'} {kept-total_net:+.4f}）")

    print(f"\n  ── 判读 ──")
    print(f"    · 看「按币 × 小时」：若 SOL/XRP 在**多数小时**都为负 ⇒ 该剔除")
    print(f"      若只有 15:00 之后为负 ⇒ 是一次行情事件，**不该据一次事件剔币**")
    print(f"    · ⚠️ 剔除会减少成交笔数与名义 ⇒ 单腿不变时**利用率下降**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
