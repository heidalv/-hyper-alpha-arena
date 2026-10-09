# -*- coding: utf-8 -*-
"""H235 挂宽敏感性：「挂宽多少才够盖住逆向选择」。

# H234 的结论与它暴露的问题

H234 验证了 σ 闸的**方向**是对的（被拦窗口毛利−逆向 −11.55 vs 放行 −8.19，
7/7 小时一致）。但同一张表显示**两组都是深度负值**：

    被拦：半价差 0.682 bp → 毛利上界 +1.897 bp，逆向幅度 +13.446 bp
    放行：半价差 0.431 bp → 毛利上界 +1.478 bp，逆向幅度  +9.672 bp

⇒ 核心算术：**赚到的价差（~2 bp）盖不住被逆向选择吃掉的（~10-13 bp）**。

这解释了一切：
  · 为什么"做市基本打平"（挂单免费 ⇒ 只有逆向选择在扣钱）
  · 为什么"大波动就大亏"（逆向幅度随波动上升）
  · 为什么 σ 闸拦不住亏损（它只拦最差的那一段，其余仍然负）

# 本脚本修正 H234 模型的两个不真实处

**不真实 ①：同一窗口假设两侧都成交。**
H234 对 `hit_buy` 与 `hit_sell` 都把毛利与逆向幅度相加 ⇒ 高估两者。
修正：**取更早被触及的那一侧**（真实情况里先成交的那条腿才是风险来源）。

**不真实 ②：逆向幅度从"我们的挂单价"起算，而实际仓位可能被推得更远。**
修正：对成交的那一侧，从挂单价起算到窗口末的**最不利价**，
并且**扣掉 2×taker fee**（若被迫过价离场）。这是保守口径。

# 核心问题：挂宽 k 倍，能不能转正

对 k ∈ {1,2,3,5,8,12,20}：
  挂单价 = `mid ∓ k × half_spread`（k=1 = 现状）
  · 触及率会下降（更宽更不容易被吃到）
  · 每次成交的毛利 = `2k × half_spread_bp`
  · 逆向幅度按同一窗口重新测（从新的挂单价起算）
⇒ 找到"毛利 − 逆向 > 0"的 k 区间，以及**该 k 下的触及率**
（若 k 很大才转正、但触及率塌到 ~0，那等于不做市）。

# 用法

    python scripts/h235_width_sensitivity.py --hours 6 --m 60
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
OUT = ROOT / "research_l1" / "out" / "h235_width_sensitivity.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
KS = [1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0]
TAKER_FEE_BP = 4.0


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
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--m", type=float, default=60.0)
    ap.add_argument("--grid", type=float, default=15.0)
    a = ap.parse_args()

    import psycopg
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

    g = {}
    for sym, ms, bid, ask in rows:
        k = int(ms // 1000 // a.grid * a.grid)
        g.setdefault(str(sym), {})[k] = (float(bid), float(ask))
    series = {s: sorted(d.items()) for s, d in g.items()}

    print("=" * 104)
    print("H235  挂宽敏感性：挂宽多少才够盖住逆向选择")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　网格 {a.grid:g}s　未来窗 {a.m:g}s　"
          f"taker fee {TAKER_FEE_BP} bp")
    print(f"  口径修正：只算**先被触及的那一侧**；逆向幅度算到窗口最不利价；"
          f"离场按 taker 计费")

    res = {}
    for sym, items in sorted(series.items()):
        bare = sym.replace("USDT", "")
        ks = [k for k, _ in items]
        vals = [v for _, v in items]
        mids = [(lo + hi) / 2.0 for lo, hi in vals]
        n = len(mids)
        if n < 100:
            continue
        rows_k = []
        for mult in KS:
            pnl, hits, advs, grosses = [], 0, [], []
            for i in range(n - 1):
                lo_f, hi_f = vals[i]
                mid_f = mids[i]
                if mid_f <= 0:
                    continue
                hs = (hi_f - lo_f) / mid_f * 1e4 / 2.0
                bid_q = mid_f * (1.0 - mult * hs / 1e4)
                ask_q = mid_f * (1.0 + mult * hs / 1e4)
                # 未来窗口
                j = i + 1
                end = ks[i] + a.m
                fut = []
                while j < n and ks[j] <= end:
                    fut.append(vals[j])
                    j += 1
                if not fut:
                    continue
                t_buy = next((t for t, (l, _h) in enumerate(fut) if l <= bid_q), None)
                t_sell = next((t for t, (_l, h) in enumerate(fut) if h >= ask_q), None)
                if t_buy is None and t_sell is None:
                    continue
                hits += 1
                # 取更早被触及的那一侧
                if t_buy is not None and (t_sell is None or t_buy <= t_sell):
                    side = "buy"
                    entry = bid_q
                    after = [l for l, _h in fut[t_buy:]]
                    worst = min(after) if after else entry
                    adv = max(0.0, (entry - worst) / entry * 1e4)
                else:
                    side = "sell"
                    entry = ask_q
                    after = [h for _l, h in fut[t_sell:]]
                    worst = max(after) if after else entry
                    adv = max(0.0, (worst - entry) / entry * 1e4)
                gross = 2.0 * mult * hs
                pnl.append(gross - adv - 2.0 * TAKER_FEE_BP)
                advs.append(adv)
                grosses.append(gross)
            tot = n - 1
            res.setdefault(bare, {})[mult] = {
                "hit_rate": round(hits / tot * 100, 2) if tot else 0.0,
                "gross": round(st.mean(grosses), 4) if grosses else 0.0,
                "adv": round(st.mean(advs), 4) if advs else 0.0,
                "net": round(st.mean(pnl), 4) if pnl else 0.0,
                "n_hit": hits,
            }
        rows_k = res[bare]
        print(f"\n  ── {bare}　（{n} 个 15s 点）")
        print(f"     {'k':>5}{'触及率':>9}{'毛利bp':>9}{'逆向bp':>9}"
              f"{'扣2×4bp后':>11}")
        for mult in KS:
            d = rows_k[mult]
            print(f"     {mult:>5.1f}{d['hit_rate']:>8.1f}%{d['gross']:>+9.3f}"
                  f"{d['adv']:>+9.3f}{d['net']:>+11.3f}")

    # ── 汇总：转正的 k ──
    print(f"\n{'━'*104}\n  一、汇总：各 k 下的（组合平均）触及率与净额\n{'━'*104}")
    print(f"\n  {'k':>5}{'触及率(中位)':>14}{'毛利bp':>10}{'逆向bp':>10}"
          f"{'扣 taker 后净bp':>17}{'转正?':>8}")
    for mult in KS:
        hr = st.median([res[s][mult]["hit_rate"] for s in res])
        gr = st.mean([res[s][mult]["gross"] for s in res])
        ad = st.mean([res[s][mult]["adv"] for s in res])
        nt = st.mean([res[s][mult]["net"] for s in res])
        print(f"  {mult:>5.1f}{hr:>13.1f}%{gr:>+10.3f}{ad:>+10.3f}"
              f"{nt:>+17.3f}{'**是**' if nt > 0 else '否':>8}")

    # ── 逐币转正点 ──
    print(f"\n{'━'*104}\n  二、逐币：净额转正的最小 k\n{'━'*104}")
    print(f"\n  {'币':<10}{'转正 k':>10}{'该 k 触及率':>14}{'该 k 净额bp':>14}")
    for s in sorted(res):
        pos = [m for m in KS if res[s][m]["net"] > 0]
        if pos:
            m0 = min(pos)
            print(f"  {s:<10}{m0:>10.1f}{res[s][m0]['hit_rate']:>13.1f}%"
                  f"{res[s][m0]['net']:>+14.3f}")
        else:
            best = max(KS, key=lambda m: res[s][m]["net"])
            print(f"  {s:<10}{'无一转正':>10}{res[s][best]['hit_rate']:>13.1f}%"
                  f"{res[s][best]['net']:>+14.3f}（最大 k={best:g} 仍负）")

    print(f"\n  ⚠️ 口径说明（必须知道才能用这张表）：")
    print(f"     · 触及率用的是 **15s 网格的 bid/ask**，比真实 tick 稀疏")
    print(f"       ⇒ 会**低估**触及率（真实市场里更细的价格会被摸到）")
    print(f"     · 无队列模型 ⇒ 触及即成交，**高估**成交率")
    print(f"     · 逆向幅度按「成交后到窗口末的最不利价」 ⇒ **保守（高估）**")
    print(f"     · 两个偏差方向相反，所以这张表是**量级参考**，不是精确收益预测")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "m": a.m, "ks": KS,
                               "by_symbol": res}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
