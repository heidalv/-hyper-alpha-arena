# -*- coding: utf-8 -*-
"""昨晚分析 第四部分：被动成交后的 markout（逆向选择直接测量）。只读。"""
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
select ts, symbol, (meta_json->>'mid_px')::float, (meta_json->>'fill_px')::float,
       lower(meta_json->>'side'), (meta_json->>'qty')::float, notional,
       (lower(coalesce(meta_json->>'flatten','false')) in ('true','1')),
       net_bp
from lane_ledger
where lane_id='mm_asterdex' and ts >= %s and ts < %s and meta_json ? 'mid_px'
order by ts
""", (W0, W1))
rows = cur.fetchall()
conn.close()

series = defaultdict(list)   # symbol -> [(ts_epoch, mid)]
for ts, sym, mid, fpx, side, qty, notional, flat, nbp in rows:
    if mid and mid > 0:
        series[sym].append((ts.timestamp(), float(mid)))
for k in series:
    series[k].sort()

p(f"腿数 {len(rows)}；中价序列点数 " + ", ".join(f"{k}={len(v)}" for k, v in sorted(series.items())))

HOR = [1, 5, 15, 30, 60, 120, 300]

def mid_at(sym, t, h):
    arr = series[sym]
    lo, hi = 0, len(arr)
    target = t + h
    while lo < hi:
        m = (lo + hi) // 2
        if arr[m][0] < target:
            lo = m + 1
        else:
            hi = m
    if lo >= len(arr):
        return None
    return arr[lo][1]

for tag, want_flat in (("成交腿(被动)", False), ("出库腿(穿价)", True)):
    p(f"\n== {tag} 之后的 markout（bp，正=对我们有利） ==")
    p(f"  {'horizon':>8}" + "".join(f"{h:>9}s" for h in HOR))
    stats = {h: [] for h in HOR}
    n_used = 0
    for ts, sym, mid, fpx, side, qty, notional, flat, nbp in rows:
        if bool(flat) != want_flat or not mid or mid <= 0:
            continue
        sgn = 1.0 if side == "buy" else -1.0
        ok = False
        for h in HOR:
            m2 = mid_at(sym, ts.timestamp(), h)
            if m2 is None:
                stats[h].append(None)
                continue
            stats[h].append(sgn * (m2 - float(mid)) / float(mid) * 1e4)
            ok = True
        if ok:
            n_used += 1
    for label, fn in (("均值", lambda a: sum(a) / len(a)),
                      ("中位", lambda a: sorted(a)[len(a) // 2]),
                      ("不利占比", lambda a: 100.0 * sum(1 for x in a if x < 0) / len(a))):
        line = f"  {label:>8}"
        for h in HOR:
            a = [x for x in stats[h] if x is not None]
            line += f"{fn(a):>10.3f}" if a else f"{'—':>10}"
        p(line)
    p(f"  （样本 {n_used}）")

p("\n== 每个 horizon 上「中价不动」的比例（>=|0.1bp| 才算动了） ==")
for tag, want_flat in (("成交腿", False), ("出库腿", True)):
    line = f"  {tag:<8}"
    for h in HOR:
        tot = moved = 0
        for ts, sym, mid, fpx, side, qty, notional, flat, nbp in rows:
            if bool(flat) != want_flat or not mid or mid <= 0:
                continue
            m2 = mid_at(sym, ts.timestamp(), h)
            if m2 is None:
                continue
            tot += 1
            if abs((m2 - float(mid)) / float(mid) * 1e4) >= 0.1:
                moved += 1
        line += f"{100.0*moved/max(tot,1):>9.1f}%"
    p(line)
p("  " + " " * 8 + "".join(f"{h:>9}s" for h in HOR))

ROOT.joinpath("logs/_tmp_timeline/overnight_analysis4.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
