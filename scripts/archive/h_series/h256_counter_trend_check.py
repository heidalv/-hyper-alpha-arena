# -*- coding: utf-8 -*-
"""H256 逆势 vs 顺势腿的期望：决定是否切 side_mode=counter_trend。

# 背景（框架纠偏）

H255 用市场 tick 直接测到：**短期反转在 30s~5min 尺度 4/4 币一致存在**
（corr −0.01~−0.07，9 个 (k,m) 组合全为负）。

账本里的 F199 也早有同一结论：逆势 **+2.11bp**、顺势 **−4.05bp**。
而我此前把这些当成"归因假象"扔掉了 —— 那是我陷入"做市赚价差"错误框架的结果。

仓库里已实现 `side_mode="counter_trend"`（涨了只挂卖、跌了只挂买），
当前却配成 `"both"`（双边做市）⇒ 赚钱的逆势腿和亏钱的顺势腿互相抵消。

# 本脚本：用账本 + tick 重测逆势/顺势腿的期望（跨窗口判据）

每条 maker 腿都有 `side` 和成交时刻 `ts`；用 tick 数据算它成交前的趋势方向：
    · 逆势腿 = 跌了买（buy after down）/ 涨了卖（sell after up）
    · 顺势腿 = 涨了买（buy after up）/ 跌了卖（sell after down）

然后看两组各自的 `net_bp`（已含 spread + price + fee）。

# 判据（本会话硬规矩）

1. 逆势 vs 顺势的**符号差**是否稳定（跨币、跨小时）
2. 报独立腿数（不是"窗口"数，账本腿天然独立）
3. 若逆势稳定为正、顺势稳定为负 ⇒ 切 counter_trend 有据
4. 若符号翻转 ⇒ 又是伪结论

# 用法

    python scripts/h256_counter_trend_check.py --hours 12
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h256_counter_trend.json"
LANE = "mm_asterdex"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT", "ADAUSDT"]
K_LOOKBACK = [30.0, 60.0, 120.0]


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
                       coalesce(spread_bp,0), coalesce(price_bp,0),
                       coalesce(net_bp,0), coalesce(notional,0)
                FROM lane_ledger
                WHERE lane_id=%s AND (meta_json->'flatten')::text='false'
                  AND ts >= now() - (%s || ' hours')::interval
                  AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, str(float(a.hours))))
            legs = cur.fetchall()

    RAW = {}
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
                    RAW[sym] = cur.fetchall()
        except Exception as e:
            print(f"  ⚠️ {sym}: {str(e)[:60]}")
    PX = {}
    for sym, recs in RAW.items():
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks = sorted(d)
        PX[sym] = (ks, [d[k] for k in ks])

    print("=" * 104)
    print("H256  逆势 vs 顺势腿的期望（决定 counter_trend）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　maker 腿 {len(legs)}　tick 覆盖 {len(PX)} 币")

    # 每条腿打标：趋势方向（用 K_LOOKBACK 里最强的信号）
    rows = []
    for sym, ts, side, sp, px_, net, notl in legs:
        tk = str(sym).upper()
        if not tk.endswith("USDT"):
            tk += "USDT"
        v = PX.get(tk)
        if not v:
            continue
        ks, mids = v
        t = int(ts.timestamp())
        # 二分找最近的 tick
        import bisect
        i = bisect.bisect_right(ks, t) - 1
        if i < 2 or mids[i] <= 0:
            continue
        rows.append({"sym": str(sym), "side": side, "spread": float(sp),
                     "price": float(px_), "net": float(net),
                     "notional": float(notl), "i": i})
    # 逐 K 重算（趋势方向随 lookback 变）
    print(f"\n  匹配 {len(rows)} 条腿")

    for K in K_LOOKBACK:
        grp = {"ct": [], "mt": []}   # counter-trend / momentum(顺势)
        for sym, ts, side, sp, px_, net, notl in legs:
            tk = str(sym).upper()
            if not tk.endswith("USDT"):
                tk += "USDT"
            v = PX.get(tk)
            if not v:
                continue
            ks, mids = v
            t = int(ts.timestamp())
            import bisect
            i = bisect.bisect_right(ks, t) - 1
            # past K 秒起点
            j = i
            while j > 0 and ks[i] - ks[j] < K:
                j -= 1
            if j == i or mids[j] <= 0:
                continue
            trend = (mids[i] - mids[j]) / mids[j] * 1e4   # >0 涨
            up = trend > 0
            is_ct = (side == "buy" and not up) or (side == "sell" and up)
            (grp["ct"] if is_ct else grp["mt"]).append(float(net))
        print(f"\n{'━'*104}\n  趋势 lookback = {K:g}s\n{'━'*104}")
        print(f"  {'组':<12}{'腿数':>7}{'net_bp均值':>12}{'中位':>9}{'为正占比':>10}")
        for lbl in ("ct", "mt"):
            v = grp[lbl]
            if not v:
                continue
            pos = sum(1 for x in v if x > 0) / len(v) * 100
            print(f"  {lbl:<12}{len(v):>7}{st.mean(v):>+12.4f}{st.median(v):>+9.4f}"
                  f"{pos:>9.1f}%")
        if grp["ct"] and grp["mt"]:
            diff = st.mean(grp["ct"]) - st.mean(grp["mt"])
            print(f"  ⇒ 逆势−顺势 = **{diff:+.4f}bp/腿**")
        # 逐小时
        print(f"\n  逐小时（net_bp 均值）：")
        print(f"  {'小时':>6}{'逆势':>10}{'顺势':>10}{'逆势为正?':>12}")
        hrs = {}
        for sym, ts, side, sp, px_, net, notl in legs:
            tk = str(sym).upper()
            if not tk.endswith("USDT"):
                tk += "USDT"
            v = PX.get(tk)
            if not v:
                continue
            ks, mids = v
            t = int(ts.timestamp())
            import bisect
            i = bisect.bisect_right(ks, t) - 1
            j = i
            while j > 0 and ks[i] - ks[j] < K:
                j -= 1
            if j == i or mids[j] <= 0:
                continue
            up = (mids[i] - mids[j]) / mids[j] * 1e4 > 0
            is_ct = (side == "buy" and not up) or (side == "sell" and up)
            h = ts.strftime("%H:00")
            hrs.setdefault(h, {"ct": [], "mt": []})
            hrs[h]["ct" if is_ct else "mt"].append(float(net))
        for h in sorted(hrs):
            d = hrs[h]
            if len(d["ct"]) < 10 or len(d["mt"]) < 10:
                continue
            mct = st.mean(d["ct"])
            mmt = st.mean(d["mt"])
            print(f"  {h:>6}{mct:>+10.4f}{mmt:>+10.4f}"
                  f"{'✓' if mct > mmt else '✗':>12}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "n": len(rows)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
