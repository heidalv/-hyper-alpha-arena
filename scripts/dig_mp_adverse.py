# -*- coding: utf-8 -*-
"""深挖③：止损入场时刻的微价偏离 vs 全体入场——验证"逆向选择"假设。只读。"""
import sys
import json
import pathlib
import bisect
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402
from backend.services.market_maker.core import microprice_skew_bp  # noqa: E402

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

# 全体入场腿（14h，带 symbol/ts/side）
cur.execute("""
    SELECT symbol, ts, meta_json->>'side'
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '14 hours'
      AND (meta_json->>'exit_path' IS NULL OR meta_json->>'exit_path' = '')
""")
all_entries = [(str(s), t, str(sd)) for s, t, sd in cur.fetchall()]

# 止损仓的入场腿（从 v2 重建逻辑简化：止损腿前最近的反侧腿们——直接用止损腿的同 symbol
# 前 10 分钟内反侧入场近似）
cur.execute("""
    SELECT ts, symbol, meta_json->>'side'
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '14 hours'
      AND meta_json->>'exit_path' = 'stop_loss_taker'
""")
stop_legs = cur.fetchall()
stop_entries = []
for ts0, sym, sd0 in stop_legs:
    pos_side = "sell" if sd0 == "buy" else "buy"
    cur.execute("""
        SELECT ts FROM lane_ledger
        WHERE event='fill' AND symbol=%s AND ts <= %s AND ts >= %s - interval '12 minutes'
          AND meta_json->>'side' = %s
        ORDER BY ts DESC LIMIT 1
    """, (sym, ts0, ts0, pos_side))
    r = cur.fetchone()
    if r:
        stop_entries.append((sym, r[0], pos_side))

syms = sorted({s for s, *_ in all_entries + stop_entries})
import datetime as dt
t0 = min(t for _, t, _ in all_entries + stop_entries) - dt.timedelta(seconds=60)
t1 = max(t for _, t, _ in all_entries + stop_entries) + dt.timedelta(seconds=60)

print("加载深度快照……", flush=True)
skew_map = {}
with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                     autocommit=True) as cm:
    with cm.cursor() as mcur:
        for sym in syms:
            mcur.execute("""
                SELECT event_ts_ms, bids, asks
                FROM asterdex_depth_snapshots
                WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s
                  AND bids IS NOT NULL AND asks IS NOT NULL
                ORDER BY event_ts_ms
            """, (sym + "USDT", int(t0.timestamp() * 1000), int(t1.timestamp() * 1000)))
            rows = mcur.fetchall()
            ts_l = []
            sk_l = []
            for ms, bids, asks in rows:
                sk = microprice_skew_bp(list(bids or []), list(asks or []))
                ts_l.append(int(ms) // 1000)
                sk_l.append(sk)
            skew_map[sym] = (ts_l, sk_l)
            print(f"  {sym}: {len(rows)} 档", flush=True)


def skew_at(sym, ts):
    tl, sl = skew_map.get(sym, ([], []))
    if not tl:
        return None
    k = bisect.bisect_right(tl, int(ts.timestamp())) - 1
    return sl[k] if k >= 0 else None


print("\n== 全体入场 vs 止损仓入场的微价偏离（bp，正=微价偏买/逆卖方向）==")
all_sk = []
for sym, t, sd in all_entries:
    v = skew_at(sym, t)
    if v is not None:
        # 统一为"入场侧逆向偏离"：买腿看 −skew（微价偏卖=我们买贵了）；卖腿看 +skew
        all_sk.append(-v if sd == "buy" else v)
stop_sk = []
for sym, t, sd in stop_entries:
    v = skew_at(sym, t)
    if v is not None:
        stop_sk.append(-v if sd == "buy" else v)

if all_sk and stop_sk:
    m_all = sum(all_sk) / len(all_sk)
    m_stop = sum(stop_sk) / len(stop_sk)
    import statistics
    print(f"  全体入场 n={len(all_sk)} 均逆向偏离={m_all:+.2f}bp 中位={statistics.median(all_sk):+.2f}")
    print(f"  止损仓入场 n={len(stop_sk)} 均逆向偏离={m_stop:+.2f}bp 中位={statistics.median(stop_sk):+.2f}")
    # Welch
    import math
    v1 = statistics.variance(all_sk) / len(all_sk)
    v2 = statistics.variance(stop_sk) / len(stop_sk)
    se = math.sqrt(v1 + v2)
    t = (m_stop - m_all) / se if se > 0 else 0
    df = (v1 + v2) ** 2 / (v1 ** 2 / (len(all_sk) - 1) + v2 ** 2 / (len(stop_sk) - 1))
    print(f"  差值={m_stop - m_all:+.2f}bp t={t:+.1f}（df≈{df:.0f}）——"
          + ("止损入场明显更逆向 ✗ 验证成立" if t > 2 else "差异不显著"))
else:
    print("  样本不足")
