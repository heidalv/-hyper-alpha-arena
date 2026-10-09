# -*- coding: utf-8 -*-
"""H240 用**精确挂单价**重做挂宽反事实（修正 H238 的代理量污染）。

# 为什么要重做

H238 用「成交价相对成交时中价的偏移」当挂宽代理，实测中位 **0.142bp**。
但那个量**混入了行情移动**（成交发生在挂单之后，中价已经动了）。

**精确的挂单价在账本里就有**：`seg_low` / `seg_high` 是**挂单存续期内的区间**，
由成交判定逻辑（`core.fill_side`）给出 —— 一腿是买腿 ⇒ 它成交在 `seg_low`
（价格跌到我们买价）；卖腿 ⇒ 成交在 `seg_high`。
而挂单时的中价 `mid_px` 也在 meta 里 ⇒

    真实挂宽 bp = (mid_px − seg_low) / mid_px × 1e4      （买腿）
    真实挂宽 bp = (seg_high − mid_px) / mid_px × 1e4     （卖腿）

**这是挂单时的量，不含之后的行情移动。** H238 的 0.142bp 是被污染的。

# 引擎公式（已核对，`runner.py:1111` + `core.compute_quote`）

    _spread_bp = half_spread × 2 / mid × 1e4     # 完整买卖价差（真实盘口）
    base = spread_mult × (_spread_bp / 2)        # spread_mult=0.5 ⇒ base = 价差/4

⇒ **k=1 表示"半挂宽 = 一个完整价差"**，即**挂在对手价之外**。
实测 `avg_base_bp ≈ 0.204`（spread_mult=1.0，当时归一到 1）⇒ 半价差 0.204bp
⇒ 完整价差 ≈ 0.41bp，与真实盘口 tick 量出的 0.43bp **一致** ✓

⇒ **引擎的价差输入是对的。我上一轮"只有一半"的推断是基于污染的代理量，撤回。**

# 反事实口径（比 H238 更干净）

对每条真实成交腿：
  · 它的**精确挂单价**从 seg 字段算出（见上）
  · `k` 倍宽后 ⇒ 新挂单价 = `mid ± k × w0`
  · 用 tick 网格的价格路径判断：在挂单存续窗内，价格是否仍触及新挂单价
  · 毛利 = `k × spread_bp`（账本实测值 × k）
  · 逆选择 = 账本实测 `price_bp`（**保持不变**，保守）
  · 净 = `k × spread_bp + price_bp`

# 用法

    python scripts/h240_width_cf_precise.py --hours 8
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h240_width_cf_precise.json"
LANE = "mm_asterdex"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
KS = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0]


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

    rows = []
    no_seg = 0
    for sym, ts, side, fpx, mpx, lo, hi, notl, nbp, sbp, pbp in legs:
        try:
            mp, nl = float(mpx), float(notl)
            lof, hif = float(lo), float(hi)
        except Exception:
            no_seg += 1
            continue
        if mp <= 0 or nl <= 0 or side not in ("buy", "sell") or lof <= 0 or hif <= 0:
            no_seg += 1
            continue
        # 精确挂宽：买腿成交在 seg_low，卖腿成交在 seg_high
        w0 = ((mp - lof) / mp * 1e4) if side == "buy" else ((hif - mp) / mp * 1e4)
        if w0 <= 0:
            no_seg += 1
            continue
        rows.append({"sym": str(sym), "ts": ts, "side": side, "mid": mp,
                     "w0": w0, "notional": nl, "net_bp": float(nbp),
                     "spread_bp": float(sbp), "price_bp": float(pbp),
                     "usd": nl * float(nbp) / 1e4})

    print("=" * 104)
    print("H240  挂宽反事实（精确挂单价，修正 H238 的代理量污染）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　入场腿 {len(legs)}　可用 {len(rows)}"
          f"（缺 seg/无效 {no_seg}）")
    ws = [r["w0"] for r in rows]
    print(f"  **精确挂宽 w0（挂单时，不含行情移动）**："
          f"P25 {sorted(ws)[len(ws)//4]:.4f}　中位 {st.median(ws):.4f}　"
          f"P75 {sorted(ws)[3*len(ws)//4]:.4f}　max {max(ws):.4f} bp")
    print(f"  对照 H238 的污染代理（成交价−中价）：中位 0.142bp")
    print(f"  spread_bp 均值 {st.mean([r['spread_bp'] for r in rows]):+.4f}"
          f"　price_bp 均值 {st.mean([r['price_bp'] for r in rows]):+.4f}"
          f"　net_bp 均值 {st.mean([r['net_bp'] for r in rows]):+.4f}")
    ratio = st.median(ws) / (st.mean([r["spread_bp"] for r in rows]) or 1e-9)
    print(f"  ⇒ 精确挂宽中位 / spread_bp 均值 = {ratio:.3f}"
          f"（引擎 `base = spread_mult × 价差/2`，spread_mult=0.5"
          f" ⇒ 期望 0.5）")

    # ── 一、线性模型 ──
    print(f"\n{'━'*104}\n  一、线性模型：净 = spread_bp×k + price_bp（上界）\n{'━'*104}")
    base = sum(r["usd"] for r in rows)
    notl_tot = sum(r["notional"] for r in rows)
    print(f"\n  {'k':>6}{'总净额$':>12}{'单腿净bp':>12}{'vs 现状$':>12}{'转正?':>8}")
    for k in KS:
        tot = sum(r["notional"] * (r["spread_bp"] * k + r["price_bp"]) / 1e4
                  for r in rows)
        print(f"  {k:>6.1f}{tot:>+12.2f}{tot/notl_tot*1e4:>+12.4f}"
              f"{tot-base:>+12.2f}{'**是**' if tot > 0 else '否':>8}")

    # ── 二、含成交损失（用精确挂单价 + 真实路径）──
    print(f"\n{'━'*104}\n  二、含成交损失：k 倍宽后哪些腿仍被触及\n{'━'*104}")
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
    M = 60.0
    print(f"  判定窗 m = {M:g}s")

    print(f"\n  {'k':>6}{'仍成交':>9}{'成交率':>9}{'总净额$':>12}{'单腿净bp':>12}"
          f"{'vs 现状$':>12}{'转正?':>8}")
    res = {}
    for k in KS:
        tot_u, tot_n, nk = 0.0, 0.0, 0
        for r in rows:
            bare = r["sym"].upper()
            tk = bare + "USDT" if not bare.endswith("USDT") else bare
            sv = series.get(tk)
            if not sv:
                continue
            ks_ = [x for x, _ in sv]
            px_ = [p for _, p in sv]
            t_sec = int(r["ts"].timestamp())
            kk = t_sec // int(a.grid) * int(a.grid)
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
            mid0 = px_[i0]
            if r["side"] == "buy":
                q = mid0 * (1.0 - r["w0"] * k / 1e4)
            else:
                q = mid0 * (1.0 + r["w0"] * k / 1e4)
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
            nk += 1
            tot_u += r["notional"] * (r["spread_bp"] * k + r["price_bp"]) / 1e4
            tot_n += r["notional"]
        wbp = tot_u / tot_n * 1e4 if tot_n else 0.0
        res[k] = {"n": nk, "rate_pct": round(nk / len(rows) * 100, 2),
                  "net_usd": round(tot_u, 2), "net_bp": round(wbp, 4)}
        print(f"  {k:>6.1f}{nk:>9}{nk/len(rows)*100:>8.1f}%{tot_u:>+12.2f}"
              f"{wbp:>+12.4f}{tot_u-base:>+12.2f}"
              f"{'**是**' if tot_u > 0 else '否':>8}")

    pos = [k for k in KS if res.get(k, {}).get("net_usd", -1) > 0]
    print(f"\n{'━'*104}\n  三、结论\n{'━'*104}")
    if pos:
        k0 = min(pos)
        print(f"\n  ⇒ **最小转正 k = {k0:g}**（成交率 {res[k0]['rate_pct']:.1f}%，"
              f"总净额 ${res[k0]['net_usd']:+.2f}）")
        print(f"     即半挂宽 ≈ {k0:.1f} × {st.median(ws):.3f} = "
              f"{k0*st.median(ws):.2f} bp")
    else:
        best = max(KS, key=lambda k: res.get(k, {}).get("net_usd", -1e9))
        print(f"\n  ⇒ **无 k 转正**（最优 k={best:g}，"
              f"总净额 ${res[best]['net_usd']:+.2f}，"
              f"成交率 {res[best]['rate_pct']:.1f}%）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "n": len(rows),
                               "median_w0_bp": round(st.median(ws), 4),
                               "base_usd": round(base, 2), "ks": KS,
                               "by_k": {str(k): v for k, v in res.items()}},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
