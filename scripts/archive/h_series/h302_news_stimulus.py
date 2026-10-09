# -*- coding: utf-8 -*-
"""H302 新闻刺激研究：news_events 对 asterdex 1m K 线的前向影响。

# 用户指令：新闻刺激。数据：market 库 news_events（3654 条，含
# impact_direction[-1,1] × impact_strength[1-5]、affected_symbols、published_at）。
# 问题：
    1. 新闻方向判得对不对（impact_direction 符号 vs 发布后前向收益符号）
    2. 发布后收益/波动的量级（1m/5m/15m/60m 窗口 vs 同币同时段基线）
    3. 按 impact_strength 分层
# 口径：受影响币取 affected_symbols（多为 BTC）；对照 = 同币当日同时刻
# 所有非新闻分钟的前向均值。BTC 新闻可作为车道四币的宏观开关信号源。

# 用法

    python scripts/h302_news_stimulus.py --days 7
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h302_news_stimulus.json"


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
    ap.add_argument("--days", type=float, default=7.0)
    a = ap.parse_args()

    import psycopg

    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT published_at, impact_direction, impact_strength,
                       affected_symbols, event_category
                FROM news_events
                WHERE published_at >= now() - (%s || ' days')::interval
                  AND impact_direction IS NOT NULL
                ORDER BY published_at
            """, (a.days,))
            events = [(r[0], float(r[1]), float(r[2] or 0), r[3] or [], r[4])
                      for r in cur.fetchall()]
            # BTC 1m K 线（新闻大多标 BTC；crypto_klines.symbol 是裸符号）
            cur.execute("""
                SELECT timestamp, close_price FROM crypto_klines
                WHERE exchange='asterdex' AND symbol='BTC' AND period='1m'
                  AND timestamp >= extract(epoch from now()) - %s*86400
                ORDER BY timestamp
            """, (a.days,))
            k = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
    print(f"  新闻 {len(events)} 条（7d）  BTC 1m K 线 {len(k)} 根")

    ts = [x[0] for x in k]
    px = [x[1] for x in k]
    import bisect

    def fwd(ev_ts, m):
        i = bisect.bisect_left(ts, ev_ts)
        j = bisect.bisect_left(ts, ev_ts + m * 60)
        if j < len(ts) and i < len(ts) and px[i] > 0:
            return (px[j] - px[i]) / px[i] * 1e4
        return None

    def vol_after(ev_ts, m):
        i = bisect.bisect_left(ts, ev_ts)
        j = bisect.bisect_left(ts, ev_ts + m * 60)
        if j - i >= 2 and i > 0:
            seg = [abs(px[t] - px[t - 1]) / px[t - 1] * 1e4 for t in range(i + 1, j)]
            return sum(seg)
        return None

    # 基线：同币所有分钟的 fwd 均值（按窗口）
    base_fwd = {}
    base_vol = {}
    for m in (1, 5, 15, 60):
        vals = []
        for i in range(0, len(ts) - m - 1, 5):
            if px[i] > 0 and px[i + m] > 0:
                vals.append((px[i + m] - px[i]) / px[i] * 1e4)
        base_fwd[m] = sum(vals) / len(vals) if vals else 0.0
        vs = []
        for i in range(0, len(ts) - m - 1, 5):
            seg = [abs(px[t] - px[t - 1]) / px[t - 1] * 1e4
                   for t in range(i + 1, i + m) if px[t] > 0 and px[t - 1] > 0]
            if seg:
                vs.append(sum(seg))
        base_vol[m] = sum(vs) / len(vs) if vs else 0.0

    print(f"\n  基线（非新闻分钟）fwd 均值: { {m: round(base_fwd[m],3) for m in (1,5,15,60)} } bp")
    print(f"  基线波动: { {m: round(base_vol[m],2) for m in (1,5,15,60)} } bp")

    results = {"n_events": len(events), "base_fwd": base_fwd, "base_vol": base_vol,
               "by_window": {}, "by_strength": {}, "direction_hit": {}}
    for m in (1, 5, 15, 60):
        dir_hits = 0
        tot = 0
        vals = []
        vols = []
        for ev in events:
            ev_ts = int(ev[0].timestamp())
            d = ev[1]
            f = fwd(ev_ts, m)
            if f is None:
                continue
            tot += 1
            if (f > 0 and d > 0) or (f < 0 and d < 0):
                dir_hits += 1
            vals.append(f)
            v = vol_after(ev_ts, m)
            if v is not None:
                vols.append(v)
        if vals:
            results["by_window"][str(m)] = {
                "n": tot, "fwd_mean_bp": round(sum(vals) / len(vals), 3),
                "base_bp": round(base_fwd[m], 3),
                "vol_mean": round(sum(vols) / len(vols), 2) if vols else None,
                "base_vol": round(base_vol[m], 2),
            }
            results["direction_hit"][str(m)] = round(dir_hits / tot, 3)
            _v = (sum(vols) / len(vols)) if vols else 0.0
            print(f"\n  fwd {m}min: n={tot}  新闻后均值 {sum(vals)/len(vals):+.3f}bp"
                  f"（基线 {base_fwd[m]:+.3f}）  方向命中率 {dir_hits}/{tot}"
                  f" = {dir_hits/tot:.0%}  波动 {_v:.1f} vs 基线 {base_vol[m]:.1f}")

    # 按强度分层（fwd 5m）
    for lo, hi, lbl in ((0, 2, "弱"), (2, 4, "中"), (4, 99, "强")):
        vals = []
        for ev in events:
            if lo <= ev[2] < hi:
                f = fwd(int(ev[0].timestamp()), 5)
                if f is not None:
                    vals.append(f)
        if vals:
            results["by_strength"][lbl] = {"n": len(vals),
                                           "fwd5_bp": round(sum(vals) / len(vals), 3)}
            print(f"  强度{lbl}（{len(vals)} 条）: fwd5 {sum(vals)/len(vals):+.3f}bp")

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
