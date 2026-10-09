# -*- coding: utf-8 -*-
"""昨晚分析 第三部分：库存方向归因 + 与前一晚/基线的三段对照。只读。"""
from __future__ import annotations

import pathlib
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
CST = timezone(timedelta(hours=8))


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

OUT = []


def p(*a):
    OUT.append(" ".join(str(x) for x in a))


conn = psycopg.connect(dsn())
conn.autocommit = True
cur = conn.cursor()

SRC = """
select ts, symbol, notional, net_bp, spread_bp, price_bp, fee_bp,
       (meta_json->>'qty')::float, lower(meta_json->>'side'),
       (lower(coalesce(meta_json->>'flatten','false')) in ('true','1')),
       (meta_json->>'mid_px')::float, net_bp*notional/1e4,
       spread_bp*notional/1e4, price_bp*notional/1e4, fee_bp*notional/1e4,
       coalesce(meta_json->>'source','')
from lane_ledger where lane_id='mm_asterdex' and ts >= %s and ts < %s order by ts
"""

W0 = datetime(2026, 9, 21, 18, 0, tzinfo=CST)
W1 = datetime(2026, 9, 22, 9, 20, tzinfo=CST)

cur.execute(SRC, (W0, W1))
rows = cur.fetchall()

p("== source 分布（窗口内） ==")
src = defaultdict(lambda: [0, 0.0])
for r in rows:
    src[r[15]][0] += 1
    src[r[15]][1] += float(r[11])
for k, (n, u) in sorted(src.items(), key=lambda x: -x[1][0]):
    p(f"  {k or '(空)':<24} n={n:<6} usd={u:+.3f}")

p("\n== 库存方向 × 行情 → 归因（每小时） ==")
# 逐币逐小时：时间加权平均库存（用相邻腿间隔加权）× 该小时中价变动
by_hour = defaultdict(lambda: defaultdict(list))   # hour -> sym -> legs
for r in rows:
    h = r[0].replace(minute=0, second=0, microsecond=0)
    by_hour[h][r[1]].append(r)

inv_all = defaultdict(float)
p(f"  {'小时':<13}{'实际price$':>12}{'库存×行情$':>13}{'价差$':>9}{'taker费$':>10}{'净额$':>10}   主要暴露")
tot_pr = tot_pred = tot_sp = tot_fe = tot_net = 0.0
for h in sorted(by_hour):
    # 先算该小时开始时的库存（用上一小时末的累加）
    start_inv = dict(inv_all)
    # 遍历本小时腿，构造库存时间序列并加权
    events = []
    inv = dict(start_inv)
    mids = {}
    for sym in by_hour[h]:
        for r in by_hour[h][sym]:
            events.append(r)
    events.sort(key=lambda r: r[0])
    wsum = defaultdict(float)
    prev_ts = h
    first_mid, last_mid = {}, {}
    for r in events:
        ts, sym, notional, nbp, sbp, pbp, fbp, qty, side, flat, mid, usd, sp, pr, fe, s = r
        dt = max(0.0, (ts - prev_ts).total_seconds())
        for k, v in inv.items():
            if v:
                wsum[k] += v * dt
        prev_ts = ts
        if mid:
            if sym not in first_mid:
                first_mid[sym] = float(mid)
            last_mid[sym] = float(mid)
            mids[sym] = float(mid)
        inv[sym] = inv.get(sym, 0.0) + (1.0 if side == "buy" else -1.0) * float(qty or 0.0)
    tail_dt = max(0.0, ((h + timedelta(hours=1)) - prev_ts).total_seconds())
    for k, v in inv.items():
        if v:
            wsum[k] += v * tail_dt
    span = 3600.0
    avg_inv = {k: v / span for k, v in wsum.items()}
    pred = 0.0
    detail = []
    for sym in sorted(set(list(first_mid) + list(last_mid))):
        if sym in first_mid and sym in last_mid and first_mid[sym] > 0:
            dpx = last_mid[sym] - first_mid[sym]
            ai = avg_inv.get(sym, 0.0)
            pred += ai * dpx
            if abs(ai) > 1e-9:
                detail.append(f"{sym}{'+' if ai>0 else '-'}{abs(ai)*last_mid[sym]:.0f}$")
    pr_usd = sum(float(r[13]) for r in events)
    sp_usd = sum(float(r[12]) for r in events)
    fe_usd = sum(float(r[14]) for r in events)
    net_usd = sum(float(r[11]) for r in events)
    tot_pr += pr_usd; tot_pred += pred; tot_sp += sp_usd; tot_fe += fe_usd; tot_net += net_usd
    inv_all = inv
    top = " ".join(detail[:4])
    p(f"  {h:%m-%d %H:%M}{pr_usd:>12.3f}{pred:>13.3f}{sp_usd:>9.3f}{fe_usd:>10.3f}{net_usd:>10.3f}   {top}")
p(f"  {'合计':<13}{tot_pr:>12.3f}{tot_pred:>13.3f}{tot_sp:>9.3f}{tot_fe:>10.3f}{tot_net:>10.3f}")

# ---------------------------------------------------------------- 三段对照
p("\n" + "=" * 100)
p("== 三晚对照（同为 18:00 → 次日 09:15 口径） ==")
p("=" * 100)
for label, a, b in [
    ("前晚 09-20", datetime(2026, 9, 20, 18, 0, tzinfo=CST), datetime(2026, 9, 21, 9, 15, tzinfo=CST)),
    ("昨晚 09-21", W0, W1),
]:
    cur.execute("""
    select count(*),
           count(*) filter (where lower(coalesce(meta_json->>'flatten','false')) in ('true','1')),
           coalesce(sum(net_bp*notional/1e4),0),
           coalesce(sum(spread_bp*notional/1e4),0),
           coalesce(sum(price_bp*notional/1e4),0),
           coalesce(sum(fee_bp*notional/1e4),0),
           coalesce(sum(net_bp*notional/1e4) filter (
               where lower(coalesce(meta_json->>'flatten','false')) not in ('true','1')),0),
           coalesce(sum(fee_bp*notional/1e4) filter (
               where lower(coalesce(meta_json->>'flatten','false')) in ('true','1')),0),
           count(distinct symbol)
    from lane_ledger where lane_id='mm_asterdex' and ts >= %s and ts < %s
    """, (a, b))
    n, fl, u, sp, pr, fe, maker_u, flat_fee, ns = cur.fetchone()
    p(f"  {label}: 腿 {n}  出库 {fl} ({100*int(fl)/max(int(n),1):.1f}%)  币 {ns}")
    p(f"      净额 {float(u):+9.3f} | 价差 {float(sp):+8.3f} | 方向 {float(pr):+9.3f} | 费 {float(fe):+8.3f}"
      f" | 成交腿净 {float(maker_u):+8.3f} | 出库taker费 {float(flat_fee):+8.3f}")
    p(f"      每腿 {float(u)/max(int(n),1):+.5f} USD   每小时 {float(u)/((b-a).total_seconds()/3600):+.3f} USD")

p("\n== 反事实 ==")
p(f"  实际净额                                  {tot_net:+9.3f}")
p(f"  若 price_bp(方向/逆向选择) = 0            {tot_net - tot_pr:+9.3f}")
p(f"  若出库 taker 费 = 0                       {tot_net - tot_fe:+9.3f}")
p(f"  若两者都为 0（只剩价差捕获）              {tot_sp:+9.3f}")
p(f"  若价差捕获 = 0（只剩方向+费）             {tot_net - tot_sp:+9.3f}")

conn.close()
ROOT.joinpath("logs/_tmp_timeline/overnight_analysis3.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
