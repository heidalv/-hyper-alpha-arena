"""H31：信号 → 择时挂单 —— 把 IC 变成钱的唯一直接路径。

## 已有

H29/H30：样本外 IC = **0.215**（主流流动性 7 币，7/7 同号，t=+9.72），
edge = IC×σ = **0.628 bp/决策**，而被动挂单成本（半价差）≈ **0.72 bp**
⇒ **差 15%**。盈亏平衡所需 IC = 0.258。

## 为什么不能直接把 IC 当钱（必须走这一步）

`edge = IC × σ` 是 Grinold 基本法则的**上界**，它假设我们**能按预测方向拿到成交**。
**被动挂单做不到**：预测「要涨」时挂买单 ⇒ 只有价格**跌下来**才成交 ⇒ 我们在
逆向选择那一侧成交（DeLise：`P(成交|中价逆向移动)=1`）。

⇒ 信号对被动挂单者的价值是**间接**的。本脚本直接测三种用法：

    **P0 基准**：不看信号，两侧都挂
    **P1 顺势挂**：预测涨 ⇒ 只挂买（期望"先跌后涨"能接到便宜货）
    **P2 反势挂**：预测涨 ⇒ 只挂卖（成交概率高，且成交后价格朝有利方向走）
    **P3 择时**：预测方向"有把握"时只挂受益的那一侧，否则两侧都挂

## 成交模型：队列消耗制（论文口径）

    · 挂单价 = 当期最优买/卖价（从 15s 桶的 `low_price`/`high_price` 近似）
    · 放量 = 该桶的 taker_sell / taker_buy（对手方主动量）
    · **成交条件**：对手方主动量 ≥ 该档位挂量（用 `bid_depth_top5`/`ask_depth_top5`
      的 1/5 作为最优档挂量的估计 —— 这是**保守**的下界，因为 top5 还包含更深的档）
    · markout **从限价算**，与论文 Table 1 / H19 / H23–H25 同口径

## 判据（事先定死）

  · 若某个策略的**每决策净额** > 基准，且改善 ≥ 0.05bp ⇒ 信号有执行价值
  · 若改善 < 0.05bp ⇒ 在本口径下不够显著，不应据此上线
  · **必须同时报成交率** —— 只看净额会选出"几乎不成交但数字好看"的策略

用法：
    .venv\\Scripts\\python.exe scripts\\h31_signal_timed_quoting.py --hours 72
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "research_l1" / "out"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--exchange", default="asterdex")
    # 只保留主流流动性（σ≤6bp）—— H30 证明另 3 个币波动 30 倍、样本少、会污染均值
    ap.add_argument("--symbols", default="BTC,ETH,BNB,SOL,DOGE,XRP,ASTER")
    ap.add_argument("--theta", type=float, default=0.5,
                    help="信号强度阈值（|标准化信号| ≥ theta 才'有把握'）")
    ap.add_argument("--leg-usd", type=float, default=30.0)
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    from h29_feature_ic_scan import build_features  # noqa: E402

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = int(args.hours * 3600_000)

    print("H31 信号择时挂单（队列消耗制成交，markout 从限价算）")
    print(f"窗口={args.hours}h  币={len(syms)}  信号阈值 θ={args.theta}  腿量=${args.leg_usd:.0f}\n")


# [F259 修正] h31_signal_timed_quoting.py 的 markout 公式（见 _fix_abs_markout.py）
# net = half + mk（mk 自带方向）
# 旧写法 half - abs(mk) 把 markout 的标准差当成成本，
# 实测 30s 上虚增 1.47bp/笔（BTC 2h 样本）。
    # 汇总：policy -> list of (net_bp, filled)
    agg = {}
    for s in syms:
        cur.execute(
            "SELECT timestamp, taker_buy_volume, taker_sell_volume,"
            "       taker_buy_count, taker_sell_count,"
            "       taker_buy_notional, taker_sell_notional,"
            "       vwap, high_price, low_price,"
            "       bid_depth_top5, ask_depth_top5, largest_trade_usd"
            "  FROM market_trades_aggregated"
            " WHERE exchange = %s AND symbol = %s AND timestamp > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY timestamp",
            (args.exchange, s, since),
        )
        rows = cur.fetchall()
        if len(rows) < 800:
            print(f"  {s:<8} 桶不足 {len(rows)} → 跳过")
            continue
        d = build_features(rows)
        if not d:
            continue
        n = d["n"]
        F = d["F"]
        hi = np.array([float(r["high_price"] or 0) for r in rows])
        lo = np.array([float(r["low_price"] or 0) for r in rows])
        tsv = np.array([float(r["taker_sell_volume"] or 0) for r in rows])
        tbv = np.array([float(r["taker_buy_volume"] or 0) for r in rows])
        bd5 = np.array([float(r["bid_depth_top5"] or 0) for r in rows])
        ad5 = np.array([float(r["ask_depth_top5"] or 0) for r in rows])
        mid = (hi + lo) / 2.0

        # 信号：flow_imb（H29 最强单特征，IC 0.118）+ depth_imb（IC 0.075）
        sig = np.nan_to_num(F["flow_imb"], nan=0.0) + np.nan_to_num(F["depth_imb"], nan=0.0)
        # 滚动标准化（只用过去 200 桶，防未来函数）
        W = 200
        mu = np.full(n, np.nan)
        sd = np.full(n, np.nan)
        for i in range(W, n):
            w = sig[i - W:i]
            mu[i] = w.mean()
            sd[i] = w.std() if w.std() > 0 else 1.0
        z = np.where(np.isfinite(sd) & (sd > 0), (sig - mu) / sd, 0.0)

        # 下一桶价格范围作为"报价后能否成交"的判据（bucket i 报价，bucket i+1 判定）
        for pol in ("P0 两侧都挂", "P1 顺势挂", "P2 反势挂", "P3 择时"):
            st = agg.setdefault(f"{s}|{pol}", {"net": [], "n_dec": 0, "n_fill": 0})

        for i in range(W, n - 1):
            zi = z[i]
            if not np.isfinite(zi):
                continue
            nxt_hi, nxt_lo = hi[i + 1], lo[i + 1]
            nxt_tsv, nxt_tbv = tsv[i + 1], tbv[i + 1]
            bid = lo[i]
            ask = hi[i]
            if bid <= 0 or ask <= 0 or ask <= bid:
                continue
            # 最优档挂量的保守估计：top5 的 1/5（top5 含更深档 ⇒ 这是下界）
            q_bid = max(1e-12, bd5[i] / 5.0)
            q_ask = max(1e-12, ad5[i] / 5.0)
            sp_half_bp = (mid[i] - bid) / mid[i] * 1e4
            strong_up = zi >= args.theta
            strong_dn = zi <= -args.theta

            for pol in ("P0 两侧都挂", "P1 顺势挂", "P2 反势挂", "P3 择时"):
                do_buy, do_sell = True, True
                if pol == "P1 顺势挂":
                    do_buy, do_sell = strong_up, strong_dn
                elif pol == "P2 反势挂":
                    do_buy, do_sell = strong_dn, strong_up
                elif pol == "P3 择时":
                    # 有把握时只挂"受益侧"：预测涨 ⇒ 只挂卖（成交后价格朝有利走）
                    if strong_up:
                        do_buy, do_sell = False, True
                    elif strong_dn:
                        do_buy, do_sell = True, False
                if not (do_buy or do_sell):
                    continue
                st = agg[f"{s}|{pol}"]
                st["n_dec"] += 1
                got = False
                if do_buy and nxt_tsv >= q_bid and nxt_lo <= bid:
                    # 成交在 bid；markout 从限价算（买单：mid 涨为有利）
                    mk = (mid[i + 1] - bid) / bid * 1e4
                    st["net"].append(sp_half_bp + mk)
                    got = True
                if do_sell and nxt_tbv >= q_ask and nxt_hi >= ask:
                    mk = (ask - mid[i + 1]) / ask * 1e4
                    st["net"].append((ask - mid[i]) / mid[i] * 1e4 + mk)
                    got = True
                if got:
                    st["n_fill"] += 1
        print(f"  {s:<8} 桶 {n:>6}  完成")

    if not agg:
        print("\n无数据")
        return 1

    # ── 汇总 ────────────────────────────────────────────────────
    policies = ["P0 两侧都挂", "P1 顺势挂", "P2 反势挂", "P3 择时"]
    print("\n[1] 各策略的每决策净额（跨币合并；**同币内可比，跨策略不可加**）")
    print("    %-14s %10s %10s %10s %12s %14s"
          % ("策略", "决策数", "成交数", "成交率", "净额bp/笔", "净额$/决策"))
    summary = {}
    for pol in policies:
        nets, ndec, nfill = [], 0, 0
        for k, v in agg.items():
            if not k.endswith(pol):
                continue
            nets.extend(v["net"])
            ndec += v["n_dec"]
            nfill += v["n_fill"]
        if not nets:
            print("    %-14s %10s" % (pol, "无成交"))
            continue
        a = np.array(nets)
        usd = a.mean() / 1e4 * args.leg_usd * (len(a) / max(1, ndec))
        summary[pol] = {"n_dec": ndec, "n_fill": nfill,
                        "fill_rate": nfill / max(1, ndec),
                        "net_bp_per_fill": float(a.mean()),
                        "net_bp_per_dec": float(a.sum() / max(1, ndec)),
                        "usd_per_dec": float(usd)}
        print("    %-14s %10d %10d %9.2f%% %12.4f %14.6f"
              % (pol, ndec, nfill, 100.0 * nfill / max(1, ndec),
                 a.mean(), a.sum() / max(1, ndec)))

    print("\n[2] 判定（基准 = P0）")
    base = summary.get("P0 两侧都挂", {}).get("net_bp_per_dec")
    if base is not None:
        print("    基准 P0 每决策净额 = %+.4f bp" % base)
        for pol in policies[1:]:
            v = summary.get(pol)
            if not v:
                continue
            d = v["net_bp_per_dec"] - base
            flag = "✓ 有执行价值" if d >= 0.05 else ("~ 不显著" if d > 0 else "✗ 更差")
            print("    %-14s 每决策净额 %+.4f bp   改善 %+.4f bp   %s"
                  % (pol, v["net_bp_per_dec"], d, flag))
    print("\n    口径提醒：")
    print("    · 成交条件用**队列消耗制**（对手方主动量 ≥ 前方挂量），")
    print("      前方挂量取 top5 的 1/5 作为**保守下界**。")
    print("    · markout 从**限价**算，与论文 Table 1 / H19 / H23–H25 同口径。")
    print("    · 桶粒度 15s ⇒ 报价时刻与实际成交时刻最多差 15s，这会**低估**")
    print("      逆向选择的即时部分（真实成交发生在桶内更早的时刻）。\n"
          "    · 只统计主流流动性 7 币（σ≤6bp）；低流动币在 H30 已证明会污染均值。")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h31_signal_timed_quoting.json"
    p.write_text(json.dumps({"hours": args.hours, "symbols": syms,
                             "theta": args.theta, "summary": summary},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")
    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
