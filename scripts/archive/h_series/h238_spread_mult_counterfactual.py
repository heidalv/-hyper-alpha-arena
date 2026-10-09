# -*- coding: utf-8 -*-
"""H238 挂宽反事实：用**真实成交**算 `spread_mult` 的边际。

# 为什么这个反事实比 H235/H236 可信

H235/H236 用「触及即成交」当成交代理 ⇒ 系统性高估逆选择（预测 −8.5bp
vs 实测 −0.99bp，差 4 倍）。原因：真实成交要求
`seg_taker_sell > 0` 且量超过队列份额（`avail × 0.30`），
价格"插一下"大概率**没有成交量**。

⇒ 本脚本**只在引擎真实成交过的那些腿上**做反事实：
   · 用账本给的 `spread_bp`（实测均值 **+0.185**）当"k=1 时的毛利"，
     按 `spread_bp × k` 缩放（挂宽线性放大 ⇒ 捕获的价差线性放大）；
   · `price_bp`（实测均值 **−1.177**）**保持不变** —— 它是成交价之后的
     行情移动，与我们的挂宽无关（这是保守假设：挂宽其实会避开最毒的成交，
     所以真实结果应当更好）；
   · 成交概率用**同一批腿自己的挂单偏移分布 + 真实价格路径**重算：
     一条腿在 k 倍宽度下还成不成交，取决于"它当时被触及的价格偏移"
     是否达到 `k × 原偏移`。

# 引擎公式（`core.compute_quote`，已核对）

    _half_spread = spread_bp / 2.0
    base = spread_mult * _half_spread          # = 我们的半挂宽
    min_width_bp 是**另一条路径**的地板（spread 模式下不生效！）
    min_edge_frac → 地板 = min_edge_frac × _half_spread（本配置为 0）

⇒ 在 spread 模式下唯一的挂宽杠杆就是 `spread_mult`。

# 用法

    python scripts/h238_spread_mult_counterfactual.py --hours 8
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h238_spread_mult.json"
LANE = "mm_asterdex"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
KS = [1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0]


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
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--grid", type=float, default=15.0)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, ts, coalesce(meta_json->>'side',''),
                       coalesce(meta_json->>'fill_px','0'),
                       coalesce(meta_json->>'mid_px','0'),
                       coalesce(meta_json->>'seg_low','0'),
                       coalesce(meta_json->>'seg_high','0'),
                       coalesce(notional,0), coalesce(net_bp,0),
                       coalesce(spread_bp,0), coalesce(price_bp,0)
                FROM lane_ledger
                WHERE lane_id=%s AND (meta_json->'flatten')::text='false'
                  AND ts >= now() - (%s || ' hours')::interval
                  AND meta_json IS NOT NULL
                ORDER BY ts ASC
            """, (LANE, str(float(a.hours))))
            legs = cur.fetchall()
    if not legs:
        print("窗口内无入场腿")
        return 1

    print("=" * 100)
    print("H238  挂宽反事实：spread_mult 的边际（用真实成交）")
    print("=" * 100)
    print(f"\n  窗口 {a.hours:g}h　入场腿 {len(legs)}")

    rows = []
    for sym, ts, side, fpx, mpx, lo, hi, notl, nbp, sbp, pbp in legs:
        try:
            fp, mp, nl = float(fpx), float(mpx), float(notl)
        except Exception:
            continue
        if fp <= 0 or mp <= 0 or nl <= 0 or side not in ("buy", "sell"):
            continue
        # 该腿被挂在哪里（离成交时中价的偏移，bp）= 我们当时的半挂宽
        off_bp = abs(fp - mp) / mp * 1e4
        rows.append({"sym": str(sym), "ts": ts, "side": side,
                     "off_bp": off_bp, "notional": nl,
                     "net_bp": float(nbp), "spread_bp": float(sbp),
                     "price_bp": float(pbp), "usd": nl * float(nbp) / 1e4})

    offs = [r["off_bp"] for r in rows]
    print(f"  实测半挂宽（离成交时中价的偏移 bp）："
          f"P25 {sorted(offs)[len(offs)//4]:.3f}　中位 {st.median(offs):.3f}　"
          f"P75 {sorted(offs)[3*len(offs)//4]:.3f}　max {max(offs):.3f}")
    print(f"  实测 spread_bp 均值 {st.mean([r['spread_bp'] for r in rows]):+.4f}"
          f"　price_bp 均值 {st.mean([r['price_bp'] for r in rows]):+.4f}"
          f"　net_bp 均值 {st.mean([r['net_bp'] for r in rows]):+.4f}")
    print(f"  ⚠️ 注意：实测半挂宽中位 {st.median(offs):.3f}bp，"
          f"而 `spread_bp` 均值仅 {st.mean([r['spread_bp'] for r in rows]):.3f}bp"
          f" ⇒ spread_bp 的口径与「离中价偏移」不同（见文末说明）")

    # ── 一、线性模型（不考虑成交损失）──
    print(f"\n{'━'*100}\n  一、线性模型：net = spread_bp×k + price_bp（**上界**，假设成交数不变）\n{'━'*100}")
    print(f"\n  {'k':>6}{'总净额$':>12}{'单腿净bp':>12}{'vs 现状$':>12}{'转正?':>8}")
    base = sum(r["usd"] for r in rows)
    for k in KS:
        tot = sum(r["notional"] * (r["spread_bp"] * k + r["price_bp"]) / 1e4
                  for r in rows)
        wbp = tot / sum(r["notional"] for r in rows) * 1e4
        print(f"  {k:>6.1f}{tot:>+12.2f}{wbp:>+12.4f}{tot-base:>+12.2f}"
              f"{'**是**' if tot > 0 else '否':>8}")

    # ── 二、含成交损失（用真实价格路径）──
    print(f"\n{'━'*100}\n  二、含成交损失：挂宽 k 倍后，哪些腿还成得交\n{'━'*100}")
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, event_ts_ms, (bid_px + ask_px) / 2.0
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND symbol = ANY(%s) AND bid_px > 0 AND ask_px > bid_px
                ORDER BY symbol, event_ts_ms ASC
            """, (str(float(a.hours) + 1.0), CUR))
            trows = cur.fetchall()
    g = {}
    for sym, ms, mid in trows:
        g.setdefault(str(sym), {})[int(ms // 1000 // a.grid * a.grid)] = float(mid)
    series = {s: sorted(d.items()) for s, d in g.items()}
    print(f"\n  tick 网格：{len(series)} 币　"
          + "　".join(f"{s}={len(v)}" for s, v in sorted(series.items())))

    # 对每条腿，用它所在网格点之后的 m 秒价格路径判断"k 倍宽是否仍被触及"
    M = 60.0
    print(f"  判定窗 m = {M:g}s（与 H235/H236 一致）")
    print(f"\n  {'k':>6}{'仍成交腿':>10}{'成交率':>9}{'总净额$':>12}"
          f"{'单腿净bp':>12}{'vs 现状$':>12}{'转正?':>8}")
    res = {}
    for k in KS:
        kept, tot_u, tot_n = [], 0.0, 0.0
        for r in rows:
            bare = r["sym"].upper()
            tk = bare + "USDT" if not bare.endswith("USDT") else bare
            sv = series.get(tk)
            if not sv:
                continue
            t_sec = int(r["ts"].timestamp())
            kk = t_sec // int(a.grid) * int(a.grid)
            ks_ = [x for x, _ in sv]
            px_ = [p for _, p in sv]
            # 二分找起点
            lo_i, hi_i = 0, len(ks_) - 1
            while lo_i < hi_i:
                m_ = (lo_i + hi_i) // 2
                if ks_[m_] < kk:
                    lo_i = m_ + 1
                else:
                    hi_i = m_
            i0 = lo_i
            if i0 >= len(ks_):
                continue
            # 我们的挂单价（k 倍宽）
            mid0 = px_[i0]
            if r["side"] == "buy":
                q = mid0 * (1.0 - r["off_bp"] * k / 1e4)
            else:
                q = mid0 * (1.0 + r["off_bp"] * k / 1e4)
            end = ks_[i0] + M
            hit = False
            j = i0
            while j < len(ks_) and ks_[j] <= end:
                if r["side"] == "buy" and px_[j] <= q:
                    hit = True
                    break
                if r["side"] == "sell" and px_[j] >= q:
                    hit = True
                    break
                j += 1
            if not hit:
                continue
            kept.append(r)
            tot_u += r["notional"] * (r["spread_bp"] * k + r["price_bp"]) / 1e4
            tot_n += r["notional"]
        nk = len(kept)
        wbp = tot_u / tot_n * 1e4 if tot_n else 0.0
        rate = nk / len(rows) * 100
        res[k] = {"n": nk, "rate_pct": round(rate, 2),
                  "total_usd": round(tot_u, 2), "w_bp": round(wbp, 4)}
        print(f"  {k:>6.1f}{nk:>10}{rate:>8.1f}%{tot_u:>+12.2f}{wbp:>+12.4f}"
              f"{tot_u-base:>+12.2f}{'**是**' if tot_u > 0 else '否':>8}")

    # ── 三、结论 ──
    print(f"\n{'━'*100}\n  三、结论\n{'━'*100}")
    pos = [k for k in KS if res.get(k, {}).get("total_usd", -1) > 0]
    if pos:
        k0 = min(pos)
        print(f"\n  ⇒ 含成交损失后，**最小转正 k = {k0:g}**"
              f"（成交率 {res[k0]['rate_pct']:.1f}%，"
              f"总净额 ${res[k0]['total_usd']:+.2f}）")
    else:
        best = max(KS, key=lambda k: res.get(k, {}).get("total_usd", -1e9))
        print(f"\n  ⇒ **没有任何 k 转正**（最优 k={best:g}，"
              f"总净额 ${res[best]['total_usd']:+.2f}，"
              f"成交率 {res[best]['rate_pct']:.1f}%）")
        print(f"     含义：单纯挂宽解决不了 —— 要么减少成交（择时），"
              f"要么改变成交的结构（只做不逆向的那一类）")

    print(f"\n  ⚠️ 口径说明（用这张表前必须知道）：")
    print(f"     · `spread_bp × k` 假设「挂宽线性放大 ⇒ 捕获价差线性放大」。")
    print(f"       真实情况里挂宽还会**改变成交的构成**（避开最毒的成交）⇒ 实际应更好。")
    print(f"     · 成交判定用 15s 网格 mid **触及**，仍是「触及即成交」的近似，")
    print(f"       但它只用来**筛掉**不再被触及的腿（保守方向）。")
    print(f"     · 未建模：挂宽变大 ⇒ 库存积累更慢 ⇒ 敞口路径改变。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "n_legs": len(rows),
                               "base_usd": round(base, 2), "m": M, "ks": KS,
                               "by_k": {str(k): v for k, v in res.items()},
                               "median_off_bp": round(st.median(offs), 4),
                               "mean_spread_bp": round(st.mean(
                                   [r["spread_bp"] for r in rows]), 4),
                               "mean_price_bp": round(st.mean(
                                   [r["price_bp"] for r in rows]), 4)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
