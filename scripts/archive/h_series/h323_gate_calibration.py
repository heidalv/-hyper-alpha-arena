# -*- coding: utf-8 -*-
"""H323 趋势闸标定：用被动成交 markout 直接选 (lookback, threshold)。

# 问题
   现行闸门 = trend_pause_bp 20 × trend_lookback 20 期（5 分钟）：只封"5 分钟顺势侧"。
   但 24h markout 证据显示毒性出现在 **15 分钟级**趋势里（强跌中卖腿 300s markout −10.2bp），
   慢速阴跌的 5 分钟窗口摸不到 20bp ⇒ 闸门全程不触发，顺势腿持续成交并失血。

# 方法（无未来函数）
   对每笔成交，用成交时点可得的 15s 中价序列算 lookback L 的趋势；
   定义"顺势侧" = 趋势方向那一侧（涨→买、跌→卖），与 trend_blocked_side 语义一致；
   对每个 (L, T)：被闸门封掉的成交子集 = 顺势侧且 |trend| ≥ T，
   比较被封锁子集 vs 放行子集的 markout（30s / 300s）。
   若被封锁子集 markout 显著更差 ⇒ 该 (L,T) 有信息量；选"封锁量不过大且封锁子集最毒"的参数。

# 用法: python scripts/h323_gate_calibration.py [--hours 24]
"""
from __future__ import annotations

import argparse
import bisect
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h323_gate_calibration.json"
LOOKBACKS = [5, 10, 20, 40, 60]      # ×15s = 75s / 150s / 5min / 10min / 15min
THRESHOLDS = [5, 8, 10, 15, 20, 30]


def read_env_dsn(market: bool) -> str:
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
    return url.replace("/alpha_arena", "/alpha_market") if market else url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--ago", type=float, default=0.0, help="窗口前移小时数（跨周期稳定性复验）")
    a = ap.parse_args()

    import psycopg2

    ca = psycopg2.connect(read_env_dsn(False))
    ca.autocommit = True
    cur = ca.cursor()
    cur.execute("""
        SELECT position_id, symbol, (meta_json->>'side') AS side,
               (meta_json->>'fill_px')::float8, (meta_json->>'mid_px')::float8,
               (meta_json->>'qty')::float8, ts
        FROM lane_ledger
        WHERE lane_id='mm_asterdex' AND event='fill'
          AND ts >= now() - make_interval(secs => %s)
          AND ts <  now() - make_interval(secs => %s)
          AND meta_json ? 'fill_px'
        ORDER BY symbol, position_id, ts
    """, ((a.hours + a.ago) * 3600.0, a.ago * 3600.0))
    raw = cur.fetchall()
    fills = []
    # 移动库存法标注每笔成交是 加仓(add) 还是 减仓(reduce)：
    # 闸门只作用于加仓侧（减仓侧 F76 豁免）——不区分会把"趋势中有利的减仓腿"
    # 误当成"会被闸门封掉的顺势腿"（2026-09-25 实测这个错误会让结论完全反号）。
    inv = {}
    for pos_id, sym, side, px, mid0, qty, ts in raw:
        key = (sym, pos_id)
        st = inv.setdefault(key, {"qty": 0.0})
        sign = 1.0 if (side or "").lower() == "buy" else -1.0
        dq = sign * (qty or 0.0)
        if abs(st["qty"]) < 1e-12:
            kind = "add"
        elif st["qty"] * dq > 0:
            kind = "add"
        else:
            kind = "reduce"
        st["qty"] += dq
        if abs(st["qty"]) < 1e-12:
            st["qty"] = 0.0
        fills.append((sym, side, px, mid0, qty, ts, kind))
    n_add = sum(1 for f in fills if f[6] == "add")
    print(f"成交腿 {len(fills)} 笔（近 {a.hours}h）：加仓 {n_add} / 减仓 {len(fills)-n_add}")

    cm = psycopg2.connect(read_env_dsn(True))
    cm.autocommit = True
    curm = cm.cursor()
    # 15s 桶中价（mid_hist 同口径：每桶最后一条报价）——供趋势口径使用
    series = {}
    for s in sorted({f[0] for f in fills}):
        bs = s if s.endswith("USDT") else s + "USDT"
        curm.execute("""
            SELECT ((event_ts_ms/1000)/15)::bigint AS b,
                   (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                   (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
            FROM asterdex_book_ticker
            WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
              AND event_ts_ms <  (extract(epoch from now())*1000 - %s*3600*1000)::bigint
              AND bid_px>0 AND ask_px>bid_px
            GROUP BY b ORDER BY b
        """, (bs, a.hours + a.ago + 1, a.ago))
        rows = curm.fetchall()
        series[s] = ([int(r[0]) for r in rows],
                     [(float(r[1]) + float(r[2])) / 2.0 for r in rows])
        # 1s 中价（markout 用）
        curm.execute("""
            SELECT (event_ts_ms/1000)::bigint AS b,
                   (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                   (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
            FROM asterdex_book_ticker
            WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
              AND event_ts_ms <  (extract(epoch from now())*1000 - %s*3600*1000)::bigint
              AND bid_px>0 AND ask_px>bid_px
            GROUP BY b ORDER BY b
        """, (bs, a.hours + a.ago + 1, a.ago))
        rows1 = curm.fetchall()
        series[s + "|1s"] = ([int(r[0]) for r in rows1],
                            [(float(r[1]) + float(r[2])) / 2.0 for r in rows1])
        print(f"  {s}: 15s 桶 {len(series[s][0])}  1s 点 {len(series[s+'|1s'][0])}")

    def mid15(sym, t):
        ks, ms = series[sym]
        j = bisect.bisect_right(ks, t // 15) - 1
        if j < 0 or (t // 15) - ks[j] > 2:
            return None
        return ms[j]

    def mid1(sym, t):
        ks, ms = series[sym + "|1s"]
        j = bisect.bisect_right(ks, t) - 1
        if j < 0 or t - ks[j] > 5:
            return None
        return ms[j]

    recs = []
    for sym, side, px, mid0, qty, ts, kind in fills:
        if sym not in series or not px or not mid0:
            continue
        t0 = int(ts.timestamp())
        sign = 1.0 if (side or "").lower() == "buy" else -1.0
        m30, m300 = mid1(sym, t0 + 30), mid1(sym, t0 + 300)
        if m30 is None or m300 is None:
            continue
        r = {"sym": sym, "side": (side or "").lower(), "notional": abs((qty or 0) * px),
             "kind": kind,
             "mk30": (m30 - px) / mid0 * 1e4 * sign,
             "mk300": (m300 - px) / mid0 * 1e4 * sign, "trend": {}}
        ok = True
        for L in LOOKBACKS:
            m0 = mid15(sym, t0)
            mL = mid15(sym, t0 - L * 15)
            if m0 is None or mL is None or mL <= 0:
                ok = False
                break
            r["trend"][L] = (m0 - mL) / mL * 1e4
        if ok:
            recs.append(r)
    print(f"可用样本 {len(recs)} 笔（加仓 {sum(1 for x in recs if x['kind']=='add')}）\n")

    def wmean(sub, key):
        tot = sum(x["notional"] for x in sub) or 1.0
        return sum(x[key] * x["notional"] for x in sub) / tot

    print("【加仓腿】闸门实际作用对象（顺势力度 ≥ T 会被封）")
    print(f"{'L(期)':>6} {'T(bp)':>6} {'封锁n':>6} {'名义%':>7} "
          f"{'封锁mk30':>9} {'封锁mk300':>10} | {'放行mk30':>9} {'放行mk300':>10}")
    table = []
    adds = [x for x in recs if x["kind"] == "add"]
    total_notional = sum(x["notional"] for x in adds) or 1.0
    for L in LOOKBACKS:
        for T in THRESHOLDS:
            blocked, allowed = [], []
            for x in adds:
                tr = x["trend"][L]
                with_trend_side = "buy" if tr > 0 else "sell"   # 顺势侧（涨→买、跌→卖）
                if x["side"] == with_trend_side and abs(tr) >= T:
                    blocked.append(x)
                else:
                    allowed.append(x)
            if len(blocked) < 15:
                continue
            med30 = sorted(x["mk30"] for x in blocked)[len(blocked) // 2]
            med300 = sorted(x["mk300"] for x in blocked)[len(blocked) // 2]
            row = {"L": L, "T": T, "n_blocked": len(blocked),
                   "notional_pct": round(sum(x["notional"] for x in blocked) / total_notional * 100, 2),
                   "blk_mk30": round(wmean(blocked, "mk30"), 3),
                   "blk_mk300": round(wmean(blocked, "mk300"), 3),
                   "med30": round(med30, 3), "med300": round(med300, 3),
                   "keep_mk30": round(wmean(allowed, "mk30"), 3),
                   "keep_mk300": round(wmean(allowed, "mk300"), 3)}
            table.append(row)
            print(f"{L:>6} {T:>6} {len(blocked):>6} {row['notional_pct']:>6.1f}% "
                  f"加权{row['blk_mk30']:>+8.3f}/中位{med30:>+7.3f}  300s加权{row['blk_mk300']:>+8.3f}/中位{med300:>+7.3f} | "
                  f"放行{row['keep_mk30']:>+8.3f} {row['keep_mk300']:>+8.3f}")

    # 参考：减仓腿（闸门豁免侧）——趋势里应为正
    reds = [x for x in recs if x["kind"] == "reduce"]
    if reds:
        print(f"\n【减仓腿·闸门豁免】n={len(reds)}  mk30={wmean(reds,'mk30'):+.3f}  "
              f"mk300={wmean(reds,'mk300'):+.3f}")

    OUT.write_text(json.dumps({"hours": a.hours, "n": len(recs), "table": table},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
