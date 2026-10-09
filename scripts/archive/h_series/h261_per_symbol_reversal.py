# -*- coding: utf-8 -*-
"""H261 逐币反转 alpha：counter_trend 框架下的选币依据（重做选币）。

# 为什么选币要重做

`h125_selector_v3` 当前选币依据 = **做市（both）框架的 `net_bp_per_cycle`**，
结论是"p25 点差 >4bp 的币全负" ⇒ 选 ASTER/XRP/SOL（窄点差）。

但框架已改成**方向性反转（counter_trend）**。反转 alpha 依赖的是
"过去趋势 → 未来反转"，与点差宽度无关（甚至宽点差币波动大、趋势幅度大，
反转 alpha 可能更强，见 H257 的幅度单调性）。

⇒ 选币必须重做：**逐币测逆势−顺势差（反转 alpha），而不是做市净额。**

# 本脚本

对每个有 tick 的币，用 H256 的方法（lookback=120s）：
    逆势 net_bp、顺势 net_bp、差、逆势为正占比、腿数

排序，看哪些币的反转 alpha 最强。

# 判据

1. 差 > +2bp 且逆势为正占比 > 70% ⇒ 反转 alpha 强，该入宇宙
2. 差 < +1bp 或逆势为负 ⇒ 反转 alpha 弱，该出宇宙
3. 腿数太少（<50）⇒ 样本不足，不下结论

# 用法

    python scripts/h261_per_symbol_reversal.py --hours 12
"""
from __future__ import annotations

import argparse
import bisect
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h261_per_symbol_reversal.json"
LANE = "mm_asterdex"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT", "ADAUSDT"]
K = 120.0


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


def market_dsn() -> str:
    return dsn().rsplit("/", 1)[0] + "/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, ts, coalesce(meta_json->>'side',''),
                       coalesce(net_bp,0), coalesce(notional,0)
                FROM lane_ledger
                WHERE lane_id=%s AND (meta_json->'flatten')::text='false'
                  AND ts >= now() - (%s || ' hours')::interval
                  AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, str(float(a.hours))))
            legs = cur.fetchall()

    # 逐币 tick（**1s 网格**，与 H256 完全一致，lookback 才是真秒）
    # ⚠️ 初版用 event_ts_ms/15000（15s bucket 号当 key），然后 `ks[i]-ks[j]<120`
    # 比较的是 bucket 号差 ⇒ 实际 lookback = 120×15 = 1800s = 30 分钟，
    # 与 H256 的 120s 不一致 ⇒ 结果矛盾（逆势全负）。必须用 1s 网格。
    G = {}
    for sym in CUR:
        try:
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                        FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                              FROM asterdex_book_ticker
                              WHERE ingest_ts >= now() - (%s || ' hours')::interval
                                AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                        ORDER BY bucket, bid_px
                    """, (str(float(a.hours) + 0.3), sym))
                    G[sym] = {int(b): ((float(x) + float(y)) / 2.0)
                              for b, x, y in cur.fetchall()}
        except Exception as e:
            print(f"  ⚠️ {sym}: {str(e)[:60]}")

    print("=" * 104)
    print("H261  逐币反转 alpha（counter_trend 框架的选币依据）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　lookback={K:g}s")

    by_sym = {}
    for sym, ts, side, net, notl in legs:
        tk = str(sym).upper()
        if not tk.endswith("USDT"):
            tk += "USDT"
        d = G.get(tk)
        if not d:
            continue
        ks = sorted(d)
        t = int(ts.timestamp())
        i = bisect.bisect_right(ks, t) - 1
        j = i
        while j > 0 and ks[i] - ks[j] < K:
            j -= 1
        if j == i or d[ks[j]] <= 0:
            continue
        trend = (d[ks[i]] - d[ks[j]]) / d[ks[j]] * 1e4
        up = trend > 0
        is_ct = (side == "buy" and not up) or (side == "sell" and up)
        bare = str(sym)
        by_sym.setdefault(bare, {"ct": [], "mt": [], "notional": 0.0})
        by_sym[bare]["ct" if is_ct else "mt"].append(float(net))
        by_sym[bare]["notional"] += float(notl)

    print(f"\n  {'symbol':<12}{'逆势腿':>7}{'顺势腿':>7}"
          f"{'逆势net':>10}{'顺势net':>10}{'差':>9}{'逆势为正%':>12}  判定")
    rows = []
    for sym in sorted(by_sym, key=lambda s: -len(by_sym[s]["ct"])):
        d = by_sym[sym]
        if not d["ct"] or not d["mt"]:
            continue
        mct = st.mean(d["ct"])
        mmt = st.mean(d["mt"])
        pos = sum(1 for x in d["ct"] if x > 0) / len(d["ct"]) * 100
        diff = mct - mmt
        if len(d["ct"]) < 50:
            verdict = "样本不足"
        elif diff > 2 and pos > 70:
            verdict = "★ 强反转"
        elif diff > 1 and pos > 60:
            verdict = "✓ 反转"
        elif diff < 0.5:
            verdict = "✗ 弱/无"
        else:
            verdict = "~ 一般"
        print(f"  {sym:<12}{len(d['ct']):>7}{len(d['mt']):>7}"
              f"{mct:>+10.4f}{mmt:>+10.4f}{diff:>+9.4f}{pos:>11.1f}%  {verdict}")
        rows.append({"symbol": sym, "ct_n": len(d["ct"]), "mt_n": len(d["mt"]),
                     "ct_net": round(mct, 4), "mt_net": round(mmt, 4),
                     "diff": round(diff, 4), "ct_pos_pct": round(pos, 2),
                     "notional": round(d["notional"], 0)})

    print(f"\n{'━'*104}\n  结论\n{'━'*104}")
    strong = [r for r in rows if r["diff"] > 2 and r["ct_pos_pct"] > 70 and r["ct_n"] >= 50]
    ok = [r for r in rows if r["diff"] > 1 and r["ct_pos_pct"] > 60 and r["ct_n"] >= 50]
    if strong:
        print(f"\n  ★ 强反转币（差>2bp 且逆势为正>70%）："
              f"{', '.join(r['symbol'] for r in strong)}")
    if ok:
        print(f"  ✓ 反转币（差>1bp 且逆势为正>60%）："
              f"{', '.join(r['symbol'] for r in ok)}")
    print(f"\n  ⚠️ 注意：只覆盖了当前宇宙 + ADA（有账本腿的币）。")
    print(f"     真正换宇宙前，需对候选池（SEI/PENDLE/VIRTUAL 等宽点差币）")
    print(f"     做**无账本腿的纯 tick 反转 corr 测试**（H255 方法），")
    print(f"     因为那些币没有做市账本腿可用。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "k": K, "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
