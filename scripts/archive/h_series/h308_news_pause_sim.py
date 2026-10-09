# -*- coding: utf-8 -*-
"""H308 新闻风险开关模拟：强新闻后 15 分钟内停新单，是否改善每腿净额。

# 依据（H302）：news_events 方向命中率 10~34%（不可预测方向），
# 新闻后收益低于基线、波动收窄（震荡 regime）——反转策略在新闻时段
# 赚不到钱只交手续费。文献同款（regime 门控第 3 条）。
# 本脚本：7 天窗口，对比
#   BASE       现行（decay3/TP12/超时120，逆 60s 趋势 θ2）
#   NEWS_PAUSE 同上 + 强新闻（|strength|≥3，BTC 新闻按全市场算）后 15 分钟不进场
# 已持仓照常走出场（只拦新单）。

# 用法

    python scripts/h308_news_pause_sim.py --hours 168
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h308_news_pause.json"
CUR = ["SOL", "DOGE", "ETH", "BNB"]


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--news-min", type=float, default=15.0, help="新闻后暂停分钟")
    a = ap.parse_args()

    import psycopg

    # 新闻事件（BTC 强新闻 = 全市场风险开关）
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT extract(epoch from published_at)::bigint
                FROM news_events
                WHERE published_at >= now() - (%s || ' hours')::interval
                  AND abs(impact_strength) >= 3
            """, (a.hours,))
            news_ts = sorted(int(r[0]) for r in cur.fetchall())
    print(f"  强新闻 {len(news_ts)} 条（{a.hours}h 窗口，|strength|≥3）")

    def in_news_pause(ts_sec: int) -> bool:
        lo, hi = 0, len(news_ts) - 1
        # 找最近一条 ≤ ts 的新闻
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if news_ts[mid] <= ts_sec:
                lo = mid
            else:
                hi = mid - 1
        if lo < 0 or news_ts[lo] > ts_sec:
            return False
        return (ts_sec - news_ts[lo]) <= a.news_min * 60

    mids_series = {}
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, bid_px
                """, (a.hours, sym + "USDT"))
                recs = cur.fetchall()
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks = sorted(d)
        mids_series[sym] = (ks, [d[k] for k in ks])
        print(f"  {sym:6} {len(ks)} 点", flush=True)

    def sim(use_pause: bool):
        stats = {"n": 0, "net_bp": [], "mae_bp": [], "paused": 0, "exits": {}}
        for sym, (ks, px) in mids_series.items():
            n = len(ks)
            last = -1e18
            for i in range(n):
                if ks[i] - last < 60.0:
                    continue
                last = ks[i]
                if use_pause and in_news_pause(ks[i]):
                    stats["paused"] += 1
                    continue
                j = i
                while j >= 0 and ks[i] - ks[j] < 60.0:
                    j -= 1
                if j < 0 or ks[i] - ks[j] < 54 or px[j] <= 0:
                    continue
                r60 = (px[i] - px[j]) / px[j] * 1e4
                if abs(r60) < 2.0:
                    continue
                sign = -1.0 if r60 > 0 else 1.0
                entry_px = px[i]
                t = i
                exit_bp = None
                exit_type = "timeout"
                while t + 1 < n and ks[t + 1] - ks[i] <= 120.0:
                    t += 1
                    mv = (px[t] - entry_px) / entry_px * 1e4 * sign
                    if mv >= 12.0:
                        exit_type = "tp"
                        exit_bp = mv
                        break
                    jj = t
                    while jj >= 0 and ks[t] - ks[jj] < 30.0:
                        jj -= 1
                    if jj >= 0 and px[jj] > 0:
                        r30 = (px[t] - px[jj]) / px[jj] * 1e4
                        if r30 * sign <= -3.0:
                            exit_type = "decay"
                            exit_bp = mv
                            break
                    if ks[t] - ks[i] >= 120.0:
                        exit_type = "timeout"
                        exit_bp = mv
                        break
                if exit_bp is None:
                    exit_bp = (px[t] - entry_px) / entry_px * 1e4 * sign
                fee = 4.0 if exit_type == "tp" else 0.0
                stats["n"] += 1
                stats["net_bp"].append(exit_bp - fee)
                mae = 0.0
                for tt in range(i, t + 1):
                    mv = (px[tt] - entry_px) / entry_px * 1e4 * sign
                    if mv < mae:
                        mae = mv
                stats["mae_bp"].append(mae)
                stats["exits"][exit_type] = stats["exits"].get(exit_type, 0) + 1
        net = stats["net_bp"]
        return {"n": stats["n"], "paused": stats["paused"],
                "net_mean_bp": round(sum(net) / len(net), 3) if net else None,
                "mae_mean_bp": round(sum(stats["mae_bp"]) / len(stats["mae_bp"]), 3) if stats["mae_bp"] else None,
                "exits": stats["exits"]}

    base = sim(use_pause=False)
    pause = sim(use_pause=True)
    print(f"\n  BASE:      {base}")
    print(f"  NEWS_PAUSE: {pause}")
    OUT.write_text(json.dumps({"base": base, "news_pause": pause,
                               "news_min": a.news_min, "n_news": len(news_ts)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
