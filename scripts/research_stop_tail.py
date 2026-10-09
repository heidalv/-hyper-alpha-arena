# -*- coding: utf-8 -*-
"""研究①：修复后 9 笔止损腿的完整解剖——触发时滞 vs 成交滑点 vs 尾部来源。

方法：库存回放重建入场均价；book_ticker 5s 网格还原止损触发时刻的 mid 轨迹，
把「−45.9bp 均值 / −90bp 尾部」拆成三部分：
  A 触发时滞（mid 已越过 avg−40 线，但下一个 15s tick 才响应期间继续跌）
  B 成交滑点（taker 按 mid±半价差成交）
  C 单 tick 跳变（一格内 mid 直接跳穿）
只读。
"""
import sys
import pathlib
import bisect
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

# 修复后止损腿
cur.execute("""
    SELECT ts, symbol, meta_json->>'side', (meta_json->>'qty')::float8,
           (meta_json->>'fill_px')::float8, (meta_json->>'mid_px')::float8, net_bp
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-28T04:56:00+00'::timestamptz
      AND meta_json->>'exit_path' = 'stop_loss_taker'
    ORDER BY ts
""")
stops = cur.fetchall()
print(f"止损腿 {len(stops)} 笔\n")

# 每笔：入场侧 qty 回放（重建 avg）+ 5s mid 轨迹
c2 = psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                     autocommit=True)
cur2 = c2.cursor()

def grid_mid(sym, t0_ms, t1_ms):
    cur2.execute("""
        SELECT event_ts_ms, (bid_px+ask_px)/2 FROM asterdex_book_ticker
        WHERE symbol=%s AND event_ts_ms BETWEEN %s AND %s
          AND bid_px>0 AND ask_px>bid_px ORDER BY event_ts_ms
    """, (sym + "USDT", t0_ms, t1_ms))
    rows = cur2.fetchall()
    # 5s 桶末 mid
    out = {}
    for ms, m in rows:
        out[int(ms) // 5000 * 5] = float(m)
    return out

print(f"{'币':<6}{'侧':<5}{'net':>7}{'入场腿数':>8}{'触发时滞s':>10}{'触发点滑点bp':>12}{'尾部bp':>8}")
for ts0, sym, side, qty, fill_px, mid_fill, net in stops:
    pos_side = "sell" if side == "buy" else "buy"
    # 入场回放
    cur.execute("""
        SELECT ts, (meta_json->>'qty')::float8, (meta_json->>'fill_px')::float8
        FROM lane_ledger
        WHERE event='fill' AND symbol=%s AND ts < %s AND ts > %s - interval '40 minutes'
          AND meta_json->>'side' = %s
          AND (meta_json->>'exit_path' IS NULL OR meta_json->>'exit_path'='')
        ORDER BY ts DESC LIMIT 12
    """, (sym, ts0, ts0, pos_side))
    legs = cur.fetchall()
    if not legs:
        print(f"{sym:<6}{side:<5}{net:>+7}bp  重建失败")
        continue
    tot_q = sum(l[1] or 0 for l in legs)
    avg_px = sum((l[1] or 0) * (l[2] or 0) for l in legs) / tot_q if tot_q else 0
    if avg_px <= 0:
        continue
    # 5s mid 轨迹：入场前 2min → 止损后 1min
    g = grid_mid(sym, int(ts0.timestamp()*1000) - 120000, int(ts0.timestamp()*1000) + 60000)
    ks = sorted(g)
    if not ks:
        continue
    # 触发线：多头 = avg−40bp；空头 = avg+40bp
    thr = avg_px * (1 - 40e-4) if pos_side == "buy" else avg_px * (1 + 40e-4)
    trig_k = None
    for k in ks:
        m = g[k]
        hit = m <= thr if pos_side == "buy" else m >= thr
        if hit:
            trig_k = k
            break
    fill_k = int(ts0.timestamp()) // 5 * 5
    trig_lag = (fill_k - trig_k) if trig_k else None
    mid_at_trig = g[trig_k] if trig_k is not None and trig_k in g else None
    # 滑点：成交价 vs 触发时刻 mid（多头 taker 卖 = 成交在 mid−半价差）
    if mid_at_trig:
        slip = (fill_px - mid_at_trig) / mid_at_trig * 1e4 * (1 if side == "buy" else -1)
    else:
        slip = None
    # 尾部：触发时刻 mid 已越过阈值多少
    tail = None
    if mid_at_trig and trig_k:
        d = (mid_at_trig - thr) / thr * 1e4
        tail = -d if pos_side == "buy" else d   # 越过阈值的深度（负=更深）
    print(f"{sym:<6}{side:<5}{net:>+7.1f}{len(legs):>7}  "
          f"{str(trig_lag):>9}  {str(round(slip,1) if slip is not None else '?'):>11}  "
          f"{str(round(tail,1) if tail is not None else '?'):>7}")
