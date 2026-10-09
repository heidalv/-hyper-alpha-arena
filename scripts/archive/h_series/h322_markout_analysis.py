# -*- coding: utf-8 -*-
"""H322 被动成交 markout 分析：赚的价差捕获 vs 付的逆选择成本。

# 为什么做这个
   车道本质 = 被动报价赚半价差（capture）− 被知情流选中后的中价漂移（adverse selection）。
   此前只测过"出口政策/闸门/模型特征"，**从没直接测量过这两项的净值**。
   若 markout 长期为负 → 报价本身在被逆选择，调出口/调参无用，必须改报价位置或毒性过滤；
   若 markout 为正而实现盈亏为负 → 出口太快/太慢，问题在持有期管理。

# 指标（全部按成交方向带符号：买为正 = 买在低位是好事）
   capture_bp  = (mid_fill − fill_px)/mid_fill × 1e4 × sign     成交瞬间的价差捕获
   markout_Δ   = (mid(t0+Δ) − fill_px)/mid_fill × 1e4 × sign    Δ 秒后的漂移（含 capture）
   drift_Δ     = markout_Δ − capture_bp                         纯中价漂移（逆选择成本）

# 用法: python scripts/h322_markout_analysis.py [--hours 24]
"""
from __future__ import annotations

import argparse
import bisect
import json
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h322_markout.json"
HORIZONS = [1, 5, 15, 30, 60, 120, 300]


def dsn_market() -> str:
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
    return url.replace("/alpha_arena", "/alpha_market")


def dsn_arena() -> str:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    a = ap.parse_args()

    import psycopg2

    # ---------- 1) 账本成交腿 ----------
    ca = psycopg2.connect(dsn_arena())
    ca.autocommit = True
    cur = ca.cursor()
    cur.execute("""
        SELECT symbol, (meta_json->>'side') AS side,
               (meta_json->>'fill_px')::float8 AS fill_px,
               (meta_json->>'mid_px')::float8 AS mid_px,
               (meta_json->>'qty')::float8 AS qty,
               COALESCE(meta_json->>'exit_path','') AS exit_path,
               ts
        FROM lane_ledger
        WHERE lane_id='mm_asterdex' AND event='fill'
          AND ts >= now() - make_interval(secs => %s)
          AND meta_json ? 'fill_px'
        ORDER BY ts
    """, (a.hours * 3600.0,))
    fills = cur.fetchall()
    print(f"成交腿 {len(fills)} 笔（近 {a.hours}h）")
    syms = sorted({f[0] for f in fills})
    print(f"币: {syms}")

    # ---------- 2) 1s 中价序列 ----------
    cm = psycopg2.connect(dsn_market())
    cm.autocommit = True
    curm = cm.cursor()
    series = {}
    for s in syms:
        bs = s if s.endswith("USDT") else s + "USDT"
        curm.execute("""
            SELECT (event_ts_ms/1000)::bigint AS b,
                   (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                   (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
            FROM asterdex_book_ticker
            WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
              AND bid_px>0 AND ask_px>bid_px
            GROUP BY b ORDER BY b
        """, (bs, a.hours + 1))
        rows = curm.fetchall()
        ks = [int(r[0]) for r in rows]
        mids = [(float(r[1]) + float(r[2])) / 2.0 for r in rows]
        series[s] = (ks, mids)
        print(f"  {s}: {len(ks)} 个 1s 中价点")

    def mid_at(s, t):
        ks, mids = series[s]
        j = bisect.bisect_right(ks, t) - 1
        if j < 0 or t - ks[j] > 5:
            return None
        return mids[j]

    # ---------- 3) 逐笔 markout ----------
    rows_out = []
    for sym, side, px, mid0, qty, ep, ts in fills:
        if sym not in series or not px or not mid0:
            continue
        t0 = int(ts.timestamp())
        sign = 1.0 if (side or "").lower() == "buy" else -1.0
        cap = (mid0 - px) / mid0 * 1e4 * sign
        # 成交前 15 分钟趋势（regime 归因：卖出腿的毒性是趋势造成还是结构性）
        m15 = mid_at(sym, t0 - 900)
        trend = ((mid0 - m15) / m15 * 1e4) if (m15 and m15 > 0) else None
        rec = {"symbol": sym, "side": side, "exit_path": ep, "cap": cap, "trend": trend,
               "notional": abs((qty or 0) * px)}
        ok = True
        for h in HORIZONS:
            m = mid_at(sym, t0 + h)
            if m is None:
                ok = False
                break
            rec[f"mk{h}"] = (m - px) / mid0 * 1e4 * sign
            rec[f"dr{h}"] = rec[f"mk{h}"] - cap
        if ok:
            rows_out.append(rec)
    print(f"可用样本 {len(rows_out)} 笔（有完整 300s 后续行情）\n")

    def agg(sub, label):
        if not sub:
            print(f"  {label:<22} 无样本")
            return None
        n = len(sub)
        tot = sum(r["notional"] for r in sub) or 1.0

        def w(k):
            return sum(r[k] * r["notional"] for r in sub) / tot
        line = {"n": n, "cap": round(w("cap"), 3)}
        for h in HORIZONS:
            line[f"mk{h}"] = round(w(f"mk{h}"), 3)
        print(f"  {label:<22} n={n:>5} 捕获={w('cap'):+6.3f}  "
              + "  ".join(f"{h}s={w(f'mk{h}'):+6.3f}" for h in (1, 5, 15, 30, 60, 120, 300)))
        return line

    print("markout（bp，名义加权；含捕获）")
    all_line = agg(rows_out, "全部")
    by_sym = {}
    print("\n按币:")
    for s in syms:
        r = agg([x for x in rows_out if x["symbol"] == s], s)
        if r:
            by_sym[s] = r
    print("\n按方向:")
    by_side = {}
    for sd in ("buy", "sell"):
        r = agg([x for x in rows_out if (x["side"] or "").lower() == sd], sd)
        if r:
            by_side[sd] = r
    print("\n按是否平仓腿（exit_path 非空=主动平仓）:")
    by_kind = {}
    for kind, pred in (("开仓/被动腿", lambda x: not x["exit_path"]),
                       ("主动平仓腿", lambda x: bool(x["exit_path"]))):
        r = agg([x for x in rows_out if pred(x)], kind)
        if r:
            by_kind[kind] = r

    # ---------- 4) regime 分层：卖腿毒性 = 趋势造成还是结构性 ----------
    print("\n按成交前 15 分钟趋势 × 方向（markout 30s，bp）:")
    buckets = [("< -20", lambda t: t < -20), ("-20~-5", lambda t: -20 <= t < -5),
               ("-5~+5", lambda t: -5 <= t <= 5), ("+5~+20", lambda t: 5 < t <= 20),
               ("> +20", lambda t: t > 20)]
    regime = {}
    for bname, pred in buckets:
        for sd in ("buy", "sell"):
            sub = [x for x in rows_out if x["trend"] is not None
                   and pred(x["trend"]) and (x["side"] or "").lower() == sd]
            if len(sub) < 20:
                continue
            tot = sum(r["notional"] for r in sub) or 1.0
            cap = sum(r["cap"] * r["notional"] for r in sub) / tot
            mk30 = sum(r["mk30"] * r["notional"] for r in sub) / tot
            mk300 = sum(r["mk300"] * r["notional"] for r in sub) / tot
            regime[f"{bname}|{sd}"] = {"n": len(sub), "cap": round(cap, 3),
                                       "mk30": round(mk30, 3), "mk300": round(mk300, 3)}
            print(f"  趋势{bname:<8} {sd:<5} n={len(sub):>4} 捕获={cap:+6.3f}"
                  f"  mk30={mk30:+6.3f}  mk300={mk300:+6.3f}")

    OUT.write_text(json.dumps({"hours": a.hours, "all": all_line, "by_symbol": by_sym,
                               "by_side": by_side, "by_kind": by_kind, "regime": regime},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
