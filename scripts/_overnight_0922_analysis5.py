# -*- coding: utf-8 -*-
"""昨晚分析 第五部分：仓位规模 vs 盈亏（杠杆效应）+ 尾部结构。只读。"""
from __future__ import annotations

import pathlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))
OUT = []


def p(*a):
    OUT.append(" ".join(str(x) for x in a))


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


import psycopg  # noqa: E402

W0 = datetime(2026, 9, 21, 18, 0, tzinfo=CST)
W1 = datetime(2026, 9, 22, 9, 20, tzinfo=CST)
conn = psycopg.connect(dsn())
conn.autocommit = True
cur = conn.cursor()
cur.execute("""
select ts, symbol, notional, net_bp, spread_bp, price_bp, fee_bp,
       (meta_json->>'qty')::float, lower(meta_json->>'side'),
       (lower(coalesce(meta_json->>'flatten','false')) in ('true','1')),
       (meta_json->>'mid_px')::float, net_bp*notional/1e4,
       spread_bp*notional/1e4, price_bp*notional/1e4, fee_bp*notional/1e4
from lane_ledger where lane_id='mm_asterdex' and ts >= %s and ts < %s order by ts
""", (W0, W1))
rows = cur.fetchall()

state = defaultdict(float)
cur_rt = {}
rts = []
for (ts, sym, notional, nbp, sbp, pbp, fbp, qty, side, flat, mid, usd, sp, pr, fe) in rows:
    q = (1.0 if side == "buy" else -1.0) * float(qty or 0.0)
    rt = cur_rt.setdefault(sym, {"sym": sym, "t0": ts, "t1": ts, "usd": 0.0, "n": 0,
                                 "peak": 0.0, "flat": False, "sp": 0.0, "pr": 0.0, "fe": 0.0})
    rt["t1"] = ts
    rt["usd"] += float(usd); rt["n"] += 1
    rt["sp"] += float(sp); rt["pr"] += float(pr); rt["fe"] += float(fe)
    rt["flat"] = rt["flat"] or bool(flat)
    state[sym] += q
    rt["peak"] = max(rt["peak"], abs(state[sym]) * float(mid or 0.0))
    if abs(state[sym]) < 1e-9:
        rts.append(rt); del cur_rt[sym]

p(f"完成往返 {len(rts)} 个（未平仓 {len(cur_rt)}）")
buckets = [(0, 300), (300, 600), (600, 900), (900, 1200), (1200, 1600), (1600, 99999)]
p(f"\n  {'峰值名义档':<16}{'个数':>6}{'占比':>7}{'合计$':>11}{'均值$':>10}{'胜率':>8}{'总腿数':>8}")
for lo, hi in buckets:
    g = [r for r in rts if lo <= r["peak"] < hi]
    if not g:
        continue
    tot = sum(r["usd"] for r in g)
    win = sum(1 for r in g if r["usd"] > 0)
    p(f"  ${lo}-{hi if hi<9999 else '∞':<10}{len(g):>6}{100*len(g)/len(rts):>6.1f}%{tot:>11.3f}"
      f"{tot/len(g):>10.4f}{100*win/len(g):>7.1f}%{sum(r['n'] for r in g):>8}")

p("\n  按峰值名义四分位（每档 25%）：")
srt = sorted(rts, key=lambda r: r["peak"])
k = len(srt) // 4
for i in range(4):
    g = srt[i * k:(i + 1) * k] if i < 3 else srt[3 * k:]
    tot = sum(r["usd"] for r in g)
    win = sum(1 for r in g if r["usd"] > 0)
    pk = sorted(r["peak"] for r in g)
    p(f"    Q{i+1} 峰值 ${pk[0]:>6.0f}~${pk[-1]:>6.0f}  n={len(g):<4} 合计 {tot:>+8.3f} "
      f"均值 {tot/len(g):>+7.4f} 胜率 {100*win/len(g):>5.1f}%")

p("\n  相关性（峰值名义 vs 单次往返盈亏）：")
import statistics
xs = [r["peak"] for r in rts]
ys = [r["usd"] for r in rts]
mx, my = statistics.mean(xs), statistics.mean(ys)
cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / len(xs)
sx = statistics.pstdev(xs); sy = statistics.pstdev(ys)
p(f"    Pearson r = {cov/(sx*sy):+.4f}   （x 均值 {mx:.0f}，y 均值 {my:+.4f}，y 标准差 {sy:.4f}）")
big = [r for r in rts if r["peak"] >= 900]
small = [r for r in rts if r["peak"] < 900]
p(f"    峰值 ≥$900 的往返 {len(big)} 个（{100*len(big)/len(rts):.1f}%）合计 {sum(r['usd'] for r in big):+.3f}")
p(f"    峰值 <$900 的往返 {len(small)} 个 合计 {sum(r['usd'] for r in small):+.3f}")

p("\n  最大 10 笔亏损（按峰值规模标注）：")
for r in sorted(rts, key=lambda r: r["usd"])[:10]:
    p(f"    {r['t0']:%m-%d %H:%M:%S} {r['sym']:<7} 峰值${r['peak']:>7.0f} 腿{r['n']:>3} "
      f"USD {r['usd']:>+8.3f} 价差 {r['sp']:>+6.3f} 方向 {r['pr']:>+7.3f} 费 {r['fe']:>+6.3f} "
      f"{'[出库]' if r['flat'] else ''}")

p("\n== 若单币敞口真的被 $536 上限封住（按峰值比例粗略反事实） ==")
eq = 268.0
cap = eq * 2.0
scale = [min(1.0, cap / r["peak"]) if r["peak"] > 0 else 1.0 for r in rts]
p(f"  平均缩放系数 {statistics.mean(scale):.3f}；按此缩放的方向项 = "
  f"{sum(r['pr']*s for r, s in zip(rts, scale)):+.3f}（实际 {sum(r['pr'] for r in rts):+.3f}）")
p("  ⚠️ 这只是量级估计（假设盈亏与规模线性、且尾部事件按同比例缩小），不作为预测。")

conn.close()
ROOT.joinpath("logs/_tmp_timeline/overnight_analysis5.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
