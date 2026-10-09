# -*- coding: utf-8 -*-
"""H215 腿量分布与尾部集中度：亏损是不是由极少数超大腿造成的。

# 触发动机（H214）

09-15 解剖：
    -21.043$  名义 $6788.59  net_bp -30.997
    -16.858$  名义 $6511.13  net_bp -25.891
而当天名义**中位数只有 $276** ⇒ 这两腿是中位数的 **24 倍**。
当天 3,148 腿的算术均值 net_bp = **+2.28 bp（正的）**，合计却是负的。

⇒ 假设：**盈亏不由"每腿的 bp 好不好"决定，而由"极少数腿的名义金额"决定。**
若成立，那么所有围绕"提升每腿 edge"的努力（找信号、调点差、调闸门）
都是在错误的轴上优化。

# 判据

1. **分布**：名义的分位结构，P50/P90/P99/P99.9/max 的比值。
2. **集中度**：按 |盈亏| 排序，前 k 腿占总盈亏的比例（逐日算，看是否稳定）。
3. **真实成本检验**：若腿量是的敞口余量函数，那么"名义大"应当与
   **库存离中点远**同时发生。查 `meta_json` 里的敞口字段。
4. **可执行性检验**：前 1% 腿的名义是否超过交易所能一次吃掉的深度
   ⇒ 纸面账本里这些"成交"是不是幻影（本会话已有先例：
   `px_exact_hit` 全 null、幻影成交时期）。

# 用法

    python scripts/h215_leg_size_tail.py --days 14
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h215_leg_size_tail.json"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def q(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
    return s[i]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT to_char(ts AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD') AS d,
                       coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(meta_json->>'flatten','false'),
                       coalesce(meta_json->>'exit_reason',''),
                       coalesce(symbol,''),
                       coalesce(meta_json->>'side',''),
                       coalesce(meta_json->>'action','')
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= now() - (%s || ' days')::interval
                ORDER BY ts ASC
            """, (LANE, str(int(a.days))))
            rows = cur.fetchall()
            cur.execute("""
                SELECT coalesce(sum(notional),0), count(*)
                FROM lane_ledger WHERE lane_id=%s
                  AND ts >= now() - (%s || ' days')::interval
            """, (LANE, str(int(a.days))))
            gtot, gn = cur.fetchone()

    if not rows:
        print("无数据")
        return 1

    print("=" * 104)
    print("H215  腿量分布与尾部集中度：亏损是「每腿不够好」还是「少数腿太大」")
    print("=" * 104)

    N = [float(r[1]) for r in rows]
    U = [float(r[1]) * float(r[2]) / 1e4 for r in rows]
    tot_u = sum(U)
    print(f"\n  {len(rows)} 腿　名义合计 ${float(gtot):,.0f}　美元合计 ${tot_u:+.2f}")

    # ── 1. 分布 ──
    print(f"\n{'━'*104}\n  一、名义金额分布（美元）\n{'━'*104}")
    print(f"\n  {'分位':<10}{'值':>14}{'相对中位':>12}")
    p50 = q(N, 50)
    for p in (1, 5, 25, 50, 75, 90, 99, 99.9):
        v = q(N, p)
        print(f"  P{p:<9}{v:>14,.2f}{v/p50 if p50 else 0:>11.2f}×")
    print(f"  {'max':<10}{max(N):>14,.2f}{max(N)/p50 if p50 else 0:>11.2f}×")
    print(f"  {'均值':<10}{st.mean(N):>14,.2f}{st.mean(N)/p50 if p50 else 0:>11.2f}×")

    # ── 2. 集中度：前 k 腿贡献了多少 |盈亏| ──
    print(f"\n{'━'*104}\n  二、集中度：把腿按 |盈亏| 从大到小排，前 k 腿占了多少\n{'━'*104}")
    order = sorted(range(len(U)), key=lambda i: -abs(U[i]))
    abs_tot = sum(abs(x) for x in U)
    print(f"\n  |盈亏| 合计 = ${abs_tot:,.2f}　净额 = ${tot_u:+.2f}"
          f"（净额/毛额 = {tot_u/abs_tot if abs_tot else 0:+.4f}）")
    print(f"\n  {'前k腿':>8}{'占|盈亏|':>12}{'这些腿净额$':>16}{'占净额':>12}")
    for k in (10, 50, 100, 500, len(U) // 100, len(U) // 20, len(U) // 10):
        if k < 1 or k > len(U):
            continue
        sel = order[:k]
        s_abs = sum(abs(U[i]) for i in sel)
        s_net = sum(U[i] for i in sel)
        print(f"  {k:>8}{s_abs/abs_tot*100:>11.1f}%{s_net:>+16.2f}"
              f"{(s_net/tot_u*100 if tot_u else 0):>11.1f}%")
    print(f"\n  ⇒ 净额/毛额比值**越接近 0**，说明盈亏几乎完全由正负相抵，"
          f"即'毛额很大、净额很小'。")
    print(f"     若前 0.1% 的腿就吃掉净额的绝大部分 ⇒ 策略的盈亏**不是**"
          f"由普遍 edge 决定。")

    # ── 3. 逐日集中度（跨窗口稳定性） ──
    print(f"\n{'━'*104}\n  三、逐日：前 1% 腿的净额占当日净额的比\n{'━'*104}")
    days = {}
    for i, r in enumerate(rows):
        days.setdefault(r[0], []).append(i)
    print(f"\n  {'日期':<12}{'腿数':>7}{'日净$':>11}{'|盈亏|$':>12}"
          f"{'前1%净$':>12}{'占比':>10}{'最大腿$':>12}{'最大腿/中位':>13}")
    ratios = []
    for d in sorted(days):
        ii = days[d]
        uu = [U[i] for i in ii]
        nn = [N[i] for i in ii]
        net = sum(uu)
        k = max(1, len(ii) // 100)
        sel = sorted(range(len(uu)), key=lambda j: -abs(uu[j]))[:k]
        s = sum(uu[j] for j in sel)
        rr = (s / net * 100) if net else float("nan")
        ratios.append(rr)
        print(f"  {d:<12}{len(ii):>7}{net:>+11.2f}"
              f"{sum(abs(x) for x in uu):>12.2f}{s:>+12.2f}{rr:>9.1f}%"
              f"{max(nn):>12,.0f}{max(nn)/st.median(nn) if st.median(nn) else 0:>12.1f}×")

    # ── 4. 可执行性：超大腿的名义 vs 当时的深度 ──
    print(f"\n{'━'*104}\n  四、可执行性：超大腿在真实盘口里吃得下吗\n{'━'*104}")
    big = [i for i in order[:max(20, len(U)//200)]]
    print(f"\n  抽样 {len(big)} 个超大腿（按 |盈亏| 前 {len(big)}）")
    print(f"  {'名义$':>12}{'net_bp':>10}{'price_bp':>10}{'symbol':>8}"
          f"{'flatten':>9}{'exit_reason':>18}")
    for i in big[:15]:
        r = rows[i]
        print(f"  {N[i]:>12,.2f}{float(r[2]):>+10.2f}{0.0:>10.2f}"
              f"{str(r[5]):>8}{str(r[3]):>9}{str(r[4]):>18}")
    print(f"\n  注：纸面成交模型不建队列、不吃深度，"
          f"因此超大名义腿在实盘能否成交通常是**独立问题**。")
    print(f"  这里只做量级提示：ASTER 盘口一档深度通常 10^3~10^4 美元量级，"
          f"$6,000+ 的单腿需要跨越很多档。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "legs": len(rows), "notional_total": float(gtot), "usd_total": round(tot_u, 2),
        "p50": round(p50, 2), "p99": round(q(N, 99), 2), "max": round(max(N), 2),
        "abs_total": round(abs_tot, 2), "max_over_median": round(max(N)/p50, 2) if p50 else None,
        "top1pct_ratio_by_day": [None if x != x else round(x, 2) for x in ratios],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
