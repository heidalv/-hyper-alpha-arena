# -*- coding: utf-8 -*-
"""昨晚(2026-09-21 18:00 → 2026-09-22 09:15)高频做市车道完整分析。只读。

口径：
  · 一行 = 一条腿（一个 fill）。USD = net_bp * notional / 1e4。
  · flatten 腿 = meta_json->>'flatten' 为 true（出库/强平腿）。
  · 往返（roundtrip）= 同一 position_id 的所有腿聚合。
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta

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

LANE = "mm_asterdex"
# 主窗口：昨晚（前一晚 18:00 起，覆盖"夜"）
W0 = datetime(2026, 9, 21, 18, 0)
W1 = datetime(2026, 9, 22, 9, 15)
# 核心夜间窗口
N0 = datetime(2026, 9, 21, 20, 0)
N1 = datetime(2026, 9, 22, 9, 15)
# 对照：前一晚同口径
P0 = datetime(2026, 9, 20, 18, 0)
P1 = datetime(2026, 9, 21, 9, 15)

OUT = []


def p(*a):
    s = " ".join(str(x) for x in a)
    OUT.append(s)
    print(s)


BASE = """
WITH leg AS (
  SELECT id, ts, symbol, position_id, notional, net_bp, spread_bp, price_bp,
         fee_bp, funding_bp, slippage_bp,
         net_bp*notional/1e4      AS usd,
         spread_bp*notional/1e4   AS spread_usd,
         price_bp*notional/1e4    AS price_usd,
         fee_bp*notional/1e4      AS fee_usd,
         funding_bp*notional/1e4  AS funding_usd,
         slippage_bp*notional/1e4 AS slip_usd,
         (lower(coalesce(meta_json->>'flatten','false')) IN ('true','1')) AS is_flat,
         (meta_json->>'fill_px')::float AS fpx,
         (meta_json->>'mid_px')::float  AS mpx,
         lower(coalesce(meta_json->>'side','')) AS side
  FROM lane_ledger
  WHERE lane_id = %(lane)s AND ts >= %(a)s AND ts < %(b)s
)
"""

conn = psycopg.connect(dsn())
conn.autocommit = True
cur = conn.cursor()

# ---------------------------------------------------------------- 0. 总量
p("=" * 100)
p(f"昨晚高频做市车道分析  lane={LANE}  主窗口 {W0:%Y-%m-%d %H:%M} → {W1:%Y-%m-%d %H:%M}")
p("=" * 100)

cur.execute(BASE + """
SELECT count(*), count(*) FILTER (WHERE is_flat), count(*) FILTER (WHERE NOT is_flat),
       coalesce(sum(usd),0), coalesce(sum(usd) FILTER (WHERE is_flat),0),
       coalesce(sum(usd) FILTER (WHERE NOT is_flat),0),
       coalesce(sum(fee_usd),0), coalesce(sum(spread_usd),0), coalesce(sum(price_usd),0),
       coalesce(sum(funding_usd),0), coalesce(sum(slip_usd),0),
       count(distinct symbol), min(ts), max(ts),
       coalesce(sum(notional),0)
FROM leg
""", {"lane": LANE, "a": W0, "b": W1})
(n, nflat, nmaker, usd, flat_usd, maker_usd, fee_usd, sp, pr, fu, sl,
 nsym, tmn, tmx, tot_notional) = cur.fetchone()
hours = (tmx - tmn).total_seconds() / 3600.0
p(f"\n【0】总量（主窗口 {hours:.2f} 小时）")
p(f"  腿数        : {n}  （成交腿 {nmaker} / 出库腿 {nflat}）")
p(f"  名义总额    : ${float(tot_notional):,.0f}   平均每腿 ${float(tot_notional)/max(n,1):.2f}")
p(f"  净额        : {float(usd):+.3f} USD   = {float(usd)/max(n,1)/ (float(tot_notional)/max(n,1)) * 1e4:+.4f} bp/腿（加权）")
p(f"  ├ 成交腿    : {float(maker_usd):+.3f} USD")
p(f"  └ 出库腿    : {float(flat_usd):+.3f} USD   （占亏损 {100*float(flat_usd)/min(float(usd),-1e-9) if usd<0 else 0:.0f}%）")
p(f"  六维分解(USD): spread {float(sp):+.3f} | price {float(pr):+.3f} | fee {float(fee_usd):+.3f} | funding {float(fu):+.3f} | slippage {float(sl):+.3f}")
p(f"  每小时      : {float(usd)/hours:+.3f} USD/h   |  {n/hours:.0f} 腿/h")
p(f"  币种        : {nsym}   {tmn:%m-%d %H:%M} → {tmx:%m-%d %H:%M}")

# ---------------------------------------------------------------- 1. 按小时
p("\n" + "=" * 100)
p("【1】按小时（完整口径：净额 = 六维之和；出库腿 = meta.flatten）")
p("=" * 100)
p(f"  {'小时':<12}{'腿数':>6}{'出库':>5}{'spread$':>9}{'price$':>9}{'fee$':>8}"
  f"{'fund$':>7}{'slip$':>7}{'净额$':>10}{'累计$':>10}{'$/h':>8}")
cur.execute(BASE + """
SELECT date_trunc('hour', ts) h, count(*), count(*) FILTER (WHERE is_flat),
       coalesce(sum(spread_usd),0), coalesce(sum(price_usd),0), coalesce(sum(fee_usd),0),
       coalesce(sum(funding_usd),0), coalesce(sum(slip_usd),0), coalesce(sum(usd),0)
FROM leg GROUP BY 1 ORDER BY 1
""", {"lane": LANE, "a": W0, "b": W1})
cum = 0.0
rows_h = cur.fetchall()
for h, hn, hf, hsp, hpr, hfe, hfu, hsl, husd in rows_h:
    cum += float(husd)
    weight = 1.0
    p(f"  {h:%m-%d %H:%M}{hn:>6}{hf:>5}{float(hsp):>9.3f}{float(hpr):>9.3f}{float(hfe):>8.3f}"
      f"{float(hfu):>7.3f}{float(hsl):>7.3f}{float(husd):>10.3f}{cum:>10.3f}{float(husd)/weight:>8.2f}")
p(f"  {'合计':<12}{n:>6}{nflat:>5}{float(sp):>9.3f}{float(pr):>9.3f}{float(fee_usd):>8.3f}"
  f"{float(fu):>7.3f}{float(sl):>7.3f}{float(usd):>10.3f}{'':>10}{float(usd)/hours:>8.2f}")

# ---------------------------------------------------------------- 2. 出库腿 vs 成交腿
p("\n" + "=" * 100)
p("【2】成交腿 vs 出库腿（各自内部构成）")
p("=" * 100)
cur.execute(BASE + """
SELECT is_flat, count(*), coalesce(sum(notional),0), coalesce(sum(spread_usd),0),
       coalesce(sum(price_usd),0), coalesce(sum(fee_usd),0), coalesce(sum(usd),0),
       coalesce(avg(net_bp),0),
       coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY net_bp),0),
       coalesce(percentile_cont(0.05) WITHIN GROUP (ORDER BY net_bp),0),
       coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY net_bp),0)
FROM leg GROUP BY 1 ORDER BY 1
""", {"lane": LANE, "a": W0, "b": W1})
for is_flat, cn, cnot, csp, cpr, cfe, cusd, cnet, cmed, c05, c95 in cur.fetchall():
    tag = "出库腿" if is_flat else "成交腿"
    p(f"  {tag}: n={cn:<6} 名义 ${float(cnot):>12,.0f}  spread {float(csp):+8.3f}  price {float(cpr):+8.3f}"
      f"  fee {float(cfe):+7.3f}  净额 {float(cusd):+9.3f}")
    p(f"          net_bp: 均值 {float(cnet):+.4f}  中位 {float(cmed):+.4f}  p05 {float(c05):+.3f}  p95 {float(c95):+.3f}")

# ---------------------------------------------------------------- 3. 出库质量看板
p("\n" + "=" * 100)
p("【3】出库质量看板（穿价 = 成交价相对当时中价不利）")
p("=" * 100)
cur.execute(BASE + """
SELECT count(*) FILTER (WHERE is_flat),
       count(*) FILTER (WHERE is_flat AND fpx IS NOT NULL AND mpx IS NOT NULL
                          AND (CASE WHEN side='buy' THEN 1 ELSE -1 END)*(fpx-mpx)/mpx*1e4 > 0),
       coalesce(percentile_cont(0.5) WITHIN GROUP (
           ORDER BY (CASE WHEN side='buy' THEN 1 ELSE -1 END)*(fpx-mpx)/mpx*1e4)
         FILTER (WHERE is_flat AND fpx IS NOT NULL AND mpx IS NOT NULL),0),
       coalesce(percentile_cont(0.9) WITHIN GROUP (
           ORDER BY (CASE WHEN side='buy' THEN 1 ELSE -1 END)*(fpx-mpx)/mpx*1e4)
         FILTER (WHERE is_flat AND fpx IS NOT NULL AND mpx IS NOT NULL),0),
       coalesce(sum(fee_usd) FILTER (WHERE is_flat),0)
FROM leg
""", {"lane": LANE, "a": W0, "b": W1})
fn, paid, med_cross, p90_cross, flat_fee = cur.fetchone()
p(f"  出库腿 {fn} 笔，其中 {paid} 笔穿价付费（{100*float(paid)/max(int(fn),1):.1f}%）")
p(f"  穿价幅度：中位 {float(med_cross):+.2f} bp   p90 {float(p90_cross):+.2f} bp")
p(f"  出库 taker 费合计：{float(flat_fee):+.3f} USD")

# ---------------------------------------------------------------- 4. 按币种
p("\n" + "=" * 100)
p("【4】按币种")
p("=" * 100)
p(f"  {'币':<8}{'腿数':>6}{'出库':>5}{'出库%':>7}{'名义$':>11}{'spread$':>9}{'price$':>9}"
  f"{'fee$':>8}{'净额$':>10}{'净bp/腿':>9}{'中位bp':>9}")
cur.execute(BASE + """
SELECT symbol, count(*), count(*) FILTER (WHERE is_flat),
       coalesce(sum(notional),0), coalesce(sum(spread_usd),0), coalesce(sum(price_usd),0),
       coalesce(sum(fee_usd),0), coalesce(sum(usd),0),
       coalesce(sum(net_bp*notional)/nullif(sum(notional),0),0),
       coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY net_bp),0)
FROM leg GROUP BY 1 ORDER BY 8
""", {"lane": LANE, "a": W0, "b": W1})
for (sym, sn, sf, snot, ssp, spr, sfe, susd, snetw, smed) in cur.fetchall():
    p(f"  {str(sym):<8}{sn:>6}{sf:>5}{100*sf/max(sn,1):>6.1f}%{float(snot):>11,.0f}"
      f"{float(ssp):>9.3f}{float(spr):>9.3f}{float(sfe):>8.3f}{float(susd):>10.3f}"
      f"{float(snetw):>9.4f}{float(smed):>9.4f}")

# ---------------------------------------------------------------- 5. 往返（position_id）
p("\n" + "=" * 100)
p("【5】往返（同一 position_id 聚合）")
p("=" * 100)
cur.execute(BASE + """
, rt AS (
  SELECT position_id, min(ts) t0, max(ts) t1,
         count(*) nleg,
         coalesce(sum(usd),0) usd,
         coalesce(sum(net_bp*notional)/nullif(sum(notional),0),0) wbp,
         bool_or(is_flat) had_flat,
         coalesce(sum(notional),0) notional,
         max(notional) peak_notional,
         min(symbol) symbol
  FROM leg GROUP BY position_id
)
SELECT count(*), coalesce(sum(usd),0),
       count(*) FILTER (WHERE usd > 0),
       coalesce(avg(usd),0),
       coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY usd),0),
       coalesce(percentile_cont(0.05) WITHIN GROUP (ORDER BY usd),0),
       coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY usd),0),
       count(*) FILTER (WHERE had_flat),
       coalesce(sum(usd) FILTER (WHERE had_flat),0),
       coalesce(sum(usd) FILTER (WHERE NOT had_flat),0),
       coalesce(percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch from (t1-t0))),0),
       coalesce(max(usd),0), coalesce(min(usd),0)
FROM rt
""", {"lane": LANE, "a": W0, "b": W1})
(rn, rusd, rwin, ravg, rmed, r05, r95, rflat_n, rflat_usd, rclean_usd, rmed_dur, rmax, rmin) = cur.fetchone()
p(f"  往返数      : {rn}   胜 {rwin} ({100*int(rwin)/max(int(rn),1):.1f}%)   净额 {float(rusd):+.3f}")
p(f"  每往返      : 均值 {float(ravg):+.4f}  中位 {float(rmed):+.4f}  p05 {float(r05):+.3f}  p95 {float(r95):+.3f}  最大 {float(rmax):+.3f}  最小 {float(rmin):+.3f}")
p(f"  含出库腿的往返: {rflat_n} 个，合计 {float(rflat_usd):+.3f}")
p(f"  纯成交的往返  : {int(rn)-int(rflat_n)} 个，合计 {float(rclean_usd):+.3f}")
p(f"  持有中位时长: {float(rmed_dur):.0f} s")

# 尾部
cur.execute(BASE + """
, rt AS (
  SELECT position_id, min(ts) t0, max(ts) t1, count(*) nleg,
         coalesce(sum(usd),0) usd, bool_or(is_flat) had_flat,
         coalesce(sum(notional),0) notional
  FROM leg GROUP BY position_id
), ranked AS (
  SELECT *, ntile(20) OVER (ORDER BY usd) AS q FROM rt
)
SELECT q, count(*), coalesce(sum(usd),0), coalesce(avg(usd),0),
       coalesce(avg(extract(epoch from (t1-t0))),0),
       count(*) FILTER (WHERE had_flat),
       coalesce(avg(peak_notional),0)
FROM ranked r
JOIN (SELECT position_id, max(notional) peak_notional FROM leg GROUP BY 1) pk
  ON pk.position_id = r.position_id
GROUP BY 1 ORDER BY 1
""", {"lane": LANE, "a": W0, "b": W1})
p("\n  按净额分 20 档（q=1 最亏）：")
p(f"    {'档':>3}{'n':>6}{'合计$':>11}{'均值$':>10}{'时长s':>8}{'含出库':>7}{'峰值名义$':>11}")
tail_sum = 0.0
for (q, qn, qusd, qavg, qdur, qflat, qpeak) in cur.fetchall():
    if q == 1:
        tail_sum = float(qusd)
    p(f"    {q:>3}{qn:>6}{float(qusd):>11.3f}{float(qavg):>10.3f}{float(qdur):>8.0f}{qflat:>7}{float(qpeak):>11.1f}")
p(f"  ⇒ 最亏 5% 档合计 {tail_sum:+.3f}；剔除后其余 {float(rusd)-tail_sum:+.3f}")

conn.close()

txt = "\n".join(OUT)
outp = ROOT / "logs" / "_tmp_timeline" / "overnight_analysis.txt"
outp.parent.mkdir(parents=True, exist_ok=True)
outp.write_text(txt, encoding="utf-8")
print(f"\n[saved] {outp}")
