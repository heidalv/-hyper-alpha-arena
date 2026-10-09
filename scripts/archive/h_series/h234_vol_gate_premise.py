# -*- coding: utf-8 -*-
"""H234 σ 闸的核心前提：「波动高就该停」到底对不对。

# 为什么这是最该验的东西

`vol_pause_sigma = 0.7` 这条闸决定了车道**什么时候完全不做市**。
实测（F341 之后）：`quoted 134/332 = 40%`，即**约 60% 的决策被它拦下**。

而它背后的前提**从未被验证过**：

    「realized_vol 超过基准 1.7 倍时，做市的期望收益为负 ⇒ 应当整车道暂停」

如果这个前提成立 ⇒ 闸门在保护我们。
如果**不成立** ⇒ 它只是砍掉了 60% 的成交机会（而价差在宽的时候更肥，
高波动期恰恰可能是做市最赚的时候）。

注意：这道闸**永远无法用账本自证** —— 它是全车道暂停，
被拦下的那部分成交**不存在**，账本里没有它们的盈亏。
⇒ 必须用**市场数据**做反事实。

# 口径（用仓库自己的函数，不再自己写第二份）

1. `asterdex_book_ticker` → 15s 网格取末值 → mid 序列
2. `core.realized_vol_bp(mid_hist, 20)`（**同一实现**）算当前波动
3. `sigma = max(0, vol / baseline − 1)`；`sigma > 0.7` ⇒ 判定"该闸在此刻会拦"
4. 反事实做市收益（**保守模型，只看被动成交、不吃队列**）：
   在时刻 t 我们两侧都挂着 `mid ± half_spread`，
   在 t→t+m 窗口内：
       · 若 `low ≤ bid` ⇒ 买单成交，随后以 `ask` 平掉
         ⇒ `edge = (ask − bid)/mid × 1e4 = 2×half_spread_bp`（**毛利，不扣费**）
         **但只有不逆向选择时才真赚** ⇒ 同时记"成交后价格是否继续跌"
       · 对称处理卖单
   ⇒ 该模型给的是**上界**（无队列、无逆向选择惩罚）。
     所以判据必须看**两个量的差**，而不是绝对收益：

        blocked_windows 的 2×half_spread_bp
      vs allowed_windows 的 2×half_spread_bp

     若被拦窗口的价差**明显更肥** ⇒ 闸门砍掉的是更值钱的成交。

# 用法

    python scripts/h234_vol_gate_premise.py --hours 6 --m 60
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h234_vol_gate_premise.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]


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


def q(v, p):
    if not v:
        return 0.0
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--m", type=float, default=60.0, help="未来窗口（秒）")
    ap.add_argument("--grid", type=float, default=15.0, help="网格（秒）")
    ap.add_argument("--window", type=int, default=20)
    a = ap.parse_args()

    import psycopg
    from backend.services.market_maker.core import realized_vol_bp

    # 基准（注册表）
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id='mm_asterdex'")
            meta = dict(cur.fetchone()[0] or {})
    base = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    par = dict(meta.get("params") or {})
    thr = float(par.get("vol_pause_sigma") or 0.0)
    win = int(par.get("vol_window") or a.window)

    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, event_ts_ms, bid_px, ask_px
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND symbol = ANY(%s) AND bid_px > 0 AND ask_px > bid_px
                ORDER BY symbol, event_ts_ms ASC
            """, (str(float(a.hours)), CUR))
            rows = cur.fetchall()

    if not rows:
        print("无数据")
        return 1

    # 15s 网格：取该网格最后一个 tick（mid / bid / ask 同源）
    g = {}
    for sym, ms, bid, ask in rows:
        k = int(ms // 1000 // a.grid * a.grid)
        g.setdefault(str(sym), {})[k] = (float(bid), float(ask))
    series = {s: sorted(d.items()) for s, d in g.items()}

    print("=" * 104)
    print("H234  σ 闸的前提检验：「波动高就该停」对不对")
    print("=" * 104)
    print(f"\n  阈值 vol_pause_sigma = {thr}　窗口 {win} 期 × {a.grid:g}s = "
          f"{win*a.grid/60:.1f} 分钟　未来窗口 m = {a.m:g}s")
    print(f"  σ = max(0, realized_vol_bp / baseline − 1)；σ > {thr} ⇒ 判定该拦")

    recs = []
    for sym, items in sorted(series.items()):
        bare = sym.replace("USDT", "")
        b = float(base.get(bare) or 0.0)
        if b <= 0:
            print(f"  ⚠️ {sym} 无基准 ⇒ 闸门对它恒不触发，跳过（无法检验）")
            continue
        ks = [k for k, _ in items]
        vals = [v for _, v in items]
        mids = [(lo + hi) / 2.0 for lo, hi in vals]
        n = len(mids)
        need = win + 1
        # 预计算每个 i 的 vol（用仓库函数，最后 need 个点）
        hist = []
        k2 = 0
        for i in range(n):
            hist.append(mids[i])
            if len(hist) < need:
                continue
            vol = realized_vol_bp(hist[-need:], win)
            sig = max(0.0, vol / b - 1.0) if b > 0 else 0.0
            if k2 < i:
                k2 = i
            while k2 < n and ks[k2] - ks[i] < a.m:
                k2 += 1
            if k2 >= n:
                break
            lo_f, hi_f = vals[i]
            mid_f = mids[i]
            fut = mids[i + 1:k2 + 1]
            if not fut:
                continue
            hs_bp = (hi_f - lo_f) / mid_f * 1e4 / 2.0      # 半价差 bp
            # 反事实：两侧挂单，窗口内是否被触及；触及后以对侧价平
            hit_buy = min(fut) <= lo_f
            hit_sell = max(fut) >= hi_f
            # 毛利（bp，不扣费、不吃队列）= 被触及的那一侧的 2×半价差
            gross = 0.0
            if hit_buy:
                gross += 2.0 * hs_bp
            if hit_sell:
                gross += 2.0 * hs_bp
            # 逆向选择：成交后价格继续朝不利方向走多远（bp）
            adv = 0.0
            if hit_buy:
                adv += max(0.0, (lo_f - min(fut)) / lo_f * 1e4)
            if hit_sell:
                adv += max(0.0, (max(fut) - hi_f) / hi_f * 1e4)
            recs.append({"sym": bare, "sec": ks[i], "sigma": sig, "vol": vol,
                         "hs_bp": hs_bp, "gross": gross, "adv": adv,
                         "hit": int(hit_buy) + int(hit_sell)})

    if not recs:
        print("无有效样本")
        return 1
    blocked = [x for x in recs if x["sigma"] > thr]
    allowed = [x for x in recs if x["sigma"] <= thr]
    print(f"\n  样本 {len(recs)}　其中判定「该拦」{len(blocked)}"
          f"（{len(blocked)/len(recs)*100:.1f}%）　「放行」{len(allowed)}")

    # ── 一、被拦 vs 放行：价差与触及率 ──
    print(f"\n{'━'*104}\n  一、被拦窗口 vs 放行窗口：报价机会的质量\n{'━'*104}")
    print(f"\n  {'组':<12}{'样本':>7}{'半价差bp中位':>14}{'被触及率':>11}"
          f"{'毛利上界bp':>13}{'逆向幅度bp':>13}{'毛利−逆向':>12}")
    for lbl, sub in (("**被拦**", blocked), ("放行", allowed)):
        if not sub:
            continue
        hs = st.median([x["hs_bp"] for x in sub])
        hr = sum(1 for x in sub if x["hit"] > 0) / len(sub) * 100
        gr = st.mean([x["gross"] for x in sub])
        ad = st.mean([x["adv"] for x in sub])
        print(f"  {lbl:<12}{len(sub):>7}{hs:>14.3f}{hr:>10.1f}%"
              f"{gr:>+13.3f}{ad:>+13.3f}{gr-ad:>+12.3f}")

    print(f"\n  ⇒ 判读：")
    if blocked and allowed:
        hb = st.median([x["hs_bp"] for x in blocked])
        ha = st.median([x["hs_bp"] for x in allowed])
        gb = st.mean([x["gross"] for x in blocked])
        ga = st.mean([x["gross"] for x in allowed])
        ab = st.mean([x["adv"] for x in blocked])
        aa = st.mean([x["adv"] for x in allowed])
        print(f"     · 被拦窗口半价差 {hb:.3f} bp vs 放行 {ha:.3f} bp"
              f"（{hb/ha if ha else float('nan'):.2f}×）")
        print(f"     · 被拦窗口毛利上界 {gb:+.3f} vs 放行 {ga:+.3f}")
        print(f"     · 被拦窗口逆向幅度 {ab:+.3f} vs 放行 {aa:+.3f}")
        if (gb - ab) > (ga - aa):
            print(f"\n     ⇒ **闸门前提不成立**：被拦窗口的（毛利−逆向）**更好**")
            print(f"        ⇒ 这道闸在砍掉本可以赚的成交。")
        else:
            print(f"\n     ⇒ **闸门前提成立**：被拦窗口的（毛利−逆向）更差")
            print(f"        ⇒ 暂停是对的方向。")

    # ── 二、逐小时稳定性（跨窗口判据）──
    print(f"\n{'━'*104}\n  二、逐小时：被拦组的（毛利−逆向）是否稳定更差\n{'━'*104}")
    hrs = {}
    for x in recs:
        hrs.setdefault(dt.datetime.fromtimestamp(x["sec"]).strftime("%H:00"),
                       []).append(x)
    print(f"\n  {'小时':<8}{'被拦n':>7}{'被拦毛利−逆向':>15}{'放行n':>7}{'放行毛利−逆向':>15}"
          f"{'被拦更好?':>11}")
    better = 0
    nh = 0
    for h in sorted(hrs):
        v = hrs[h]
        b = [x for x in v if x["sigma"] > thr]
        al = [x for x in v if x["sigma"] <= thr]
        if len(b) < 5 or len(al) < 5:
            continue
        mb = st.mean([x["gross"] - x["adv"] for x in b])
        ma = st.mean([x["gross"] - x["adv"] for x in al])
        nh += 1
        if mb > ma:
            better += 1
        print(f"  {h:<8}{len(b):>7}{mb:>+15.3f}{len(al):>7}{ma:>+15.3f}"
              f"{'是' if mb > ma else '否':>11}")
    if nh:
        print(f"\n  ⇒ 被拦组更好的小时数 = **{better}/{nh}**")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "thr": thr, "window": win, "m": a.m, "hours": a.hours,
        "n": len(recs), "n_blocked": len(blocked), "n_allowed": len(allowed),
        "blocked": {"hs_median": round(st.median([x["hs_bp"] for x in blocked]), 4),
                    "gross": round(st.mean([x["gross"] for x in blocked]), 4),
                    "adv": round(st.mean([x["adv"] for x in blocked]), 4)}
        if blocked else None,
        "allowed": {"hs_median": round(st.median([x["hs_bp"] for x in allowed]), 4),
                    "gross": round(st.mean([x["gross"] for x in allowed]), 4),
                    "adv": round(st.mean([x["adv"] for x in allowed]), 4)}
        if allowed else None,
        "hours_blocked_better": better, "hours_compared": nh,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
