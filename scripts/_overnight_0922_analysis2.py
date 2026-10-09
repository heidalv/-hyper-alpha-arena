# -*- coding: utf-8 -*-
"""昨晚分析 第二部分：库存/权益曲线/往返重建/行情对照。只读。"""
from __future__ import annotations

import pathlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]


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

W0 = datetime(2026, 9, 21, 18, 0)
W1 = datetime(2026, 9, 22, 9, 15)
OUT = []


def p(*a):
    s = " ".join(str(x) for x in a)
    OUT.append(s)


conn = psycopg.connect(dsn())
conn.autocommit = True
cur = conn.cursor()

# ---------------------------------------------------------- 取全部腿
cur.execute("""
select ts, symbol, notional, net_bp, spread_bp, price_bp, fee_bp,
       (meta_json->>'qty')::float, lower(meta_json->>'side'),
       (lower(coalesce(meta_json->>'flatten','false')) in ('true','1')),
       (meta_json->>'mid_px')::float,
       net_bp*notional/1e4
from lane_ledger
where lane_id='mm_asterdex' and ts >= %s and ts < %s
order by ts
""", (W0, W1))
legs = cur.fetchall()
p(f"腿数 {len(legs)}")

# ---------------------------------------------------------- A. 库存重建
p("\n" + "=" * 100)
p("【A】库存重建（每条腿的 qty/side 累加）")
p("=" * 100)
inv = defaultdict(float)
last_mid = {}
sym_stats = defaultdict(lambda: {"max_long": 0.0, "max_short": 0.0, "max_notional": 0.0,
                                 "flips": 0, "zero_ts": None, "timeline": []})
inv_series = []          # (ts, {sym: signed notional})
for (ts, sym, notional, nbp, sbp, pbp, fbp, qty, side, is_flat, mid, usd) in legs:
    sgn = 1.0 if side == "buy" else -1.0
    before = inv[sym]
    inv[sym] = before + sgn * float(qty or 0.0)
    if mid:
        last_mid[sym] = float(mid)
    ntl = abs(inv[sym]) * float(last_mid.get(sym) or 0.0)
    st = sym_stats[sym]
    st["max_long"] = max(st["max_long"], inv[sym])
    st["max_short"] = min(st["max_short"], inv[sym])
    st["max_notional"] = max(st["max_notional"], ntl)
    if before * inv[sym] < 0:
        st["flips"] += 1
    inv_series.append((ts, {k: v * float(last_mid.get(k) or 0.0) for k, v in inv.items()}))

p(f"  {'币':<8}{'期末qty':>12}{'最大多qty':>12}{'最大空qty':>12}{'峰值|名义|$':>13}{'穿越0次':>8}")
for sym in sorted(sym_stats):
    st = sym_stats[sym]
    p(f"  {sym:<8}{inv[sym]:>12.4f}{st['max_long']:>12.4f}{st['max_short']:>12.4f}"
      f"{st['max_notional']:>13.1f}{st['flips']:>8}")

# 总敞口（绝对值之和）与净敞口
cur.execute("""
select ts, symbol, (meta_json->>'qty')::float, lower(meta_json->>'side'),
       (meta_json->>'mid_px')::float
from lane_ledger where lane_id='mm_asterdex' and ts >= %s and ts < %s order by ts
""", (W0, W1))
inv2 = defaultdict(float)
mids = {}
gross_max, net_max, gross_series = 0.0, 0.0, []
for ts, sym, qty, side, mid in cur.fetchall():
    inv2[sym] += (1.0 if side == "buy" else -1.0) * float(qty or 0.0)
    if mid:
        mids[sym] = float(mid)
    gross = sum(abs(v) * mids.get(k, 0.0) for k, v in inv2.items())
    net = sum(v * mids.get(k, 0.0) for k, v in inv2.items())
    gross_max = max(gross_max, gross)
    net_max = max(net_max, abs(net))
    gross_series.append((ts, gross, net))
p(f"\n  峰值总敞口 ${gross_max:,.0f}   峰值净敞口 ${net_max:,.0f}   （车道权益 ≈ $268）")
p(f"  ⇒ 峰值总敞口/权益 = {gross_max/268:.2f}x    峰值净敞口/权益 = {net_max/268:.2f}x")

# 每小时末敞口
p("\n  每小时末总敞口/净敞口($)：")
byhour = {}
for ts, gross, net in gross_series:
    byhour[ts.replace(minute=0, second=0, microsecond=0)] = (gross, net)
for h in sorted(byhour):
    g, n = byhour[h]
    p(f"    {h:%m-%d %H:%M}   gross {g:>8.0f}   net {n:>+8.0f}")

# ---------------------------------------------------------- B. 往返重建（库存归零分段）
p("\n" + "=" * 100)
p("【B】往返重建：按「库存回到 0」切分")
p("=" * 100)
cur.execute("""
select ts, symbol, notional, net_bp, spread_bp, price_bp, fee_bp,
       (meta_json->>'qty')::float, lower(meta_json->>'side'),
       (lower(coalesce(meta_json->>'flatten','false')) in ('true','1')),
       net_bp*notional/1e4
from lane_ledger where lane_id='mm_asterdex' and ts >= %s and ts < %s order by ts
""", (W0, W1))
rows = cur.fetchall()
state = defaultdict(float)
cur_rt = {}
rts = []
for (ts, sym, notional, nbp, sbp, pbp, fbp, qty, side, is_flat, usd) in rows:
    sgn = 1.0 if side == "buy" else -1.0
    q = sgn * float(qty or 0.0)
    if sym not in cur_rt:
        cur_rt[sym] = {"t0": ts, "usd": 0.0, "n": 0, "peak": 0.0, "flat": False,
                       "sp": 0.0, "pr": 0.0, "fe": 0.0, "sym": sym}
    rt = cur_rt[sym]
    rt["t1"] = ts
    rt["usd"] += float(usd)
    rt["n"] += 1
    rt["sp"] += float(sbp) * float(notional) / 1e4
    rt["pr"] += float(pbp) * float(notional) / 1e4
    rt["fe"] += float(fbp) * float(notional) / 1e4
    rt["flat"] = rt["flat"] or bool(is_flat)
    state[sym] += q
    rt["peak"] = max(rt["peak"], abs(state[sym]) * float(notional / max(abs(q), 1e-12)))
    if abs(state[sym]) < 1e-9:
        rts.append(rt)
        del cur_rt[sym]

open_rt = list(cur_rt.values())
p(f"  完成往返 {len(rts)} 个；未平仓 {len(open_rt)} 个（期末仍持仓）")
tot = sum(r["usd"] for r in rts)
wins = [r for r in rts if r["usd"] > 0]
loss = [r for r in rts if r["usd"] <= 0]
p(f"  完成往返净额 {tot:+.3f}   胜 {len(wins)} ({100*len(wins)/max(len(rts),1):.1f}%)   "
  f"盈 {sum(r['usd'] for r in wins):+.3f}   亏 {sum(r['usd'] for r in loss):+.3f}")
if rts:
    usds = sorted(r["usd"] for r in rts)
    durs = sorted((r["t1"] - r["t0"]).total_seconds() for r in rts)
    def pct(a, q):
        return a[min(len(a) - 1, int(q * len(a)))]
    p(f"  每往返 USD: 均值 {sum(usds)/len(usds):+.4f}  中位 {pct(usds,0.5):+.4f}  "
      f"p05 {pct(usds,0.05):+.3f}  p95 {pct(usds,0.95):+.3f}  最差 {usds[0]:+.3f}  最好 {usds[-1]:+.3f}")
    p(f"  持有秒数  : 中位 {pct(durs,0.5):.0f}  p90 {pct(durs,0.9):.0f}  最长 {durs[-1]:.0f}")
    nflat = sum(1 for r in rts if r["flat"])
    p(f"  含出库腿的往返 {nflat} 个 ({100*nflat/len(rts):.1f}%)，合计 {sum(r['usd'] for r in rts if r['flat']):+.3f}")
    p(f"  纯被动平完的往返 {len(rts)-nflat} 个，合计 {sum(r['usd'] for r in rts if not r['flat']):+.3f}")
    # 尾部
    k = max(1, len(usds) // 20)
    p(f"\n  最差 5%（{k} 个）合计 {sum(usds[:k]):+.3f}（占全部 {100*sum(usds[:k])/min(tot,-1e-9) if tot<0 else 0:.0f}%）"
      f"；剔除后其余 {tot - sum(usds[:k]):+.3f}")
    worst = sorted(rts, key=lambda r: r["usd"])[:8]
    p(f"  {'开始':<14}{'币':<7}{'腿':>4}{'USD':>9}{'秒':>8}{'出库':>5}{'spread$':>9}{'price$':>9}{'fee$':>8}")
    for r in worst:
        p(f"  {r['t0']:%m-%d %H:%M:%S}  {r['sym']:<7}{r['n']:>4}{r['usd']:>9.3f}"
          f"{(r['t1']-r['t0']).total_seconds():>8.0f}{'Y' if r['flat'] else '':>5}"
          f"{r['sp']:>9.3f}{r['pr']:>9.3f}{r['fe']:>8.3f}")
    best = sorted(rts, key=lambda r: -r["usd"])[:5]
    p("\n  最好的 5 个：")
    for r in best:
        p(f"  {r['t0']:%m-%d %H:%M:%S}  {r['sym']:<7}{r['n']:>4}{r['usd']:>9.3f}"
          f"{(r['t1']-r['t0']).total_seconds():>8.0f}{'Y' if r['flat'] else '':>5}"
          f"{r['sp']:>9.3f}{r['pr']:>9.3f}{r['fe']:>8.3f}")

p("\n  未平仓（期末仍持仓）：")
for r in open_rt:
    p(f"  {r['t0']:%m-%d %H:%M:%S} {r['sym']:<7} legs={r['n']:>4} usd={r['usd']:+.3f} "
      f"sp={r['sp']:+.3f} pr={r['pr']:+.3f} fe={r['fe']:+.3f}")

# ---------------------------------------------------------- C. 行情对照
p("\n" + "=" * 100)
p("【C】行情：各币每小时中价变动（bp）与当小时净额")
p("=" * 100)
buckets = defaultdict(lambda: defaultdict(list))
for (ts, sym, notional, nbp, sbp, pbp, fbp, qty, side, is_flat, mid, usd) in legs:
    h = ts.replace(minute=0, second=0, microsecond=0)
    if mid:
        buckets[h][sym].append(float(mid))
symset = sorted({l[1] for l in legs})
p(f"  {'小时':<13}" + "".join(f"{s+' bp':>11}" for s in symset) + f"{'该小时净额$':>13}")
hour_usd = defaultdict(float)
for (ts, sym, notional, nbp, sbp, pbp, fbp, qty, side, is_flat, mid, usd) in legs:
    hour_usd[ts.replace(minute=0, second=0, microsecond=0)] += float(usd)
for h in sorted(buckets):
    line = f"  {h:%m-%d %H:%M}  "
    for s in symset:
        v = buckets[h].get(s) or []
        if len(v) >= 2 and v[0] > 0:
            line += f"{10000*(v[-1]/v[0]-1):>+11.1f}"
        else:
            line += f"{'—':>11}"
    line += f"{hour_usd[h]:>13.3f}"
    p(line)

conn.close()
ROOT.joinpath("logs/_tmp_timeline/overnight_analysis2.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
