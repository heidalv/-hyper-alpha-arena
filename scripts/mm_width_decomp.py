"""离线复算：实盘挂宽为何比模型窄 ~1.6 倍（F189 窗口 12:57~14:02）。

[F206 2026-09-15] 背景与三个候选
--------------------------------------------------
同口径对照已经拿到硬数字：F189 配置下
    模型最终挂宽 **8.36bp**（冻结 28.2%、基准 10.39bp）
    实盘实测挂宽 **~5.1bp**（13:01 读数 买 5.5 / 卖 4.7）
⇒ 实盘窄约 **1.6 倍** ✗。车道已被日亏闸门停住，实时读数暂时采不到，所以**离线复算**：
用行情 + 账本重建的**真实库存路径**，按 `compute_quote` **同一套公式**逐快照算挂宽，
再分场景拆开，看是谁把均值压下去的：

  S0 两侧都算（= 模型口径）            → 期望 ≈ 8.4bp
  S1 只算**减仓侧**（= 实盘常态，F184 实测单边报价 70%）→ 若 ≈5bp ⇒ **主因是"单边 + 偏斜"** ✓
  S2 不算库存偏斜（k_inv=0）           → 看偏斜本身贡献多少
  S3 不套冻结档（frozen_width=0）       → 看冻结档贡献多少（离线实测该小时 23.7%）
  S4 叠加 vol_pause 拦截（σ>1.0 的快照整个不报）→ 看闸门贡献多少

关键机制（已由代码确认）：
  `inv_ratio = 仓位名义 / (equity × max_net_directional_ratio)`（runner.py:494 ✓）
  equity≈$225~270 且一条腿≈$225~280 ⇒ **`inv_ratio` 经常饱和到 ±1** ✓
  ⇒ 偏斜把一侧拉到 `base×1.5`、另一侧压到 `base×0.5` ✓
  ⇒ 只要**只挂减仓那一侧**（单边报价），读数就只有 0.5×base ⇒ 这正是 1.6× 的来源假设 ✓。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.database.connection import MarketSessionLocal, SessionLocal  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    QuoteParams, compute_quote, realized_vol_bp, trend_move_bp,
)

SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL"]
TZ = timezone(timedelta(hours=8))
# 注册表里锚定的波动基准（F108c：实盘与模型必须同源）
VB = {"BNB": 1.480484, "BTC": 1.245663, "ETH": 1.648216, "SOL": 1.988472, "XRP": 1.975667}
EQUITY = 250.0            # 该窗口权益约 $225~270
DIRECTIONAL_CAP = 1.0     # max_net_directional_ratio

# F189 当时上线的配置（现已回滚，故这里显式写死并留档）
F189 = dict(w_base_bp=12.0, k_vol=0.3, k_inv=0.5, frozen_width_bp=3.0,
            frozen_max_move_bp=8.0, frozen_lookback=120, min_width_reduce_bp=6.0,
            min_width_bp=3.0, max_width_bp=60.0)


def _a(sql: str, **p) -> List[dict]:
    with SessionLocal() as s:
        s.execute(text("SET statement_timeout = 60000"))
        return [dict(r) for r in s.execute(text(sql), p).mappings().all()]


def _dt(v) -> datetime:
    d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
    return d.replace(tzinfo=TZ) if d.tzinfo is None else d


def main() -> int:
    a_s, b_s = "2026-09-15T12:50:00+08:00", "2026-09-15T14:05:00+08:00"
    a, b = datetime.fromisoformat(a_s), datetime.fromisoformat(b_s)
    print(f"离线挂宽复算 · {a_s[11:16]}~{b_s[11:16]} · 配置=F189(w_base=12,k_inv=0.5,"
          f"floor_reduce=6,frozen=3@8bp,vol_pause_sigma=1.0)")

    # 1) 从账本重建**真实库存路径**（逐笔，side+qty 在 meta_json 里）
    fills = _a("""SELECT ts, symbol, notional, meta_json FROM lane_ledger
                  WHERE lane_id='mm_asterdex' AND event='fill'
                    AND ts >= :a AND ts <= :b ORDER BY ts""", a=a, b=b)
    ev: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for f in fills:
        meta = f.get("meta_json") or {}
        q = float(meta.get("qty") or 0)
        if not q:
            mid = float(meta.get("mid_px") or 0) or None
            q = (float(f["notional"] or 0) / mid) if mid else 0.0
        signed = q if str(meta.get("side") or "").lower().startswith("b") else -q
        prev = ev[f["symbol"]][-1][1] if ev[f["symbol"]] else 0.0
        ev[f["symbol"]].append((_dt(f["ts"]).timestamp(), prev + signed))
    print(f"  重建库存路径：{len(fills)} 笔成交")

    # 2) 行情 + 逐快照复算
    lo = int((a.timestamp() - 3600) * 1000)
    hi = int(b.timestamp() * 1000)
    stats: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    frozen_n = tot_n = 0
    for s in SYMS:
        rows = []
        with MarketSessionLocal() as ses:
            ses.execute(text("SET statement_timeout = 60000"))
            for r in ses.execute(text(
                    """SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots
                       WHERE symbol=:s AND timestamp >= :a AND timestamp <= :b
                       ORDER BY timestamp"""), {"s": s, "a": lo, "b": hi}).mappings().all():
                if r["best_bid"] and r["best_ask"]:
                    rows.append((int(r["timestamp"]),
                                 (float(r["best_bid"]) + float(r["best_ask"])) / 2))
        if len(rows) < 150:
            continue
        mids = [m for _t, m in rows]
        et = [x[0] for x in ev.get(s) or []]
        eq = [x[1] for x in ev.get(s) or []]
        flb = int(F189["frozen_lookback"])
        win = [(t, m) for t, m in rows if a.timestamp() * 1000 <= t <= b.timestamp() * 1000]
        for i, (t, mid) in enumerate(win):
            j = i + (len(rows) - len(win))           # 在完整序列里的位置
            if j < 25:
                continue
            hist = mids[max(0, j - 240):j + 1]
            # σ 归一（与 runner.py:1527 同一公式）
            vc = realized_vol_bp(hist, 20)
            vb = VB.get(s, 0.0)
            sigma = max(0.0, vc / vb - 1.0) if vb > 0 else 0.0
            # slow_range = 近 flb 期的单步最大移动（runner.py:678-684 同一公式）
            seg = hist[-(flb + 1):]
            mv = max((abs(seg[k + 1] - seg[k]) / seg[k] * 1e4
                      for k in range(len(seg) - 1) if seg[k] > 0), default=0.0)
            # 库存偏离度（runner.py:494 同一公式）
            k = int(np.searchsorted(et, t / 1000.0, "right")) - 1
            qty = eq[k] if k >= 0 else 0.0
            inv = max(-1.0, min(1.0, (qty * mid) / max(1e-9, EQUITY * DIRECTIONAL_CAP)))
            trend = trend_move_bp(hist, 60)
            tot_n += 1
            for name, over in (
                ("S0_双侧(模型口径)", {}),
                ("S2_无偏斜", {"k_inv": 0.0}),
                ("S3_无冻结档", {"frozen_width_bp": None}),
            ):
                p = dict(F189)
                p.update(over)
                q = compute_quote(symbol=s, mid=mid, sigma_norm=sigma, inv_ratio=inv,
                                  slow_range_bp=mv, trend_bp=trend, params=QuoteParams(**p))
                if q is None:
                    continue
                stats[name][s].append((q.w_bid_bp + q.w_ask_bp) / 2)
                stats[name]["_all"].append((q.w_bid_bp + q.w_ask_bp) / 2)
                if name == "S0_双侧(模型口径)":
                    if q.mode == "frozen":
                        frozen_n += 1
                    # S1：只取**减仓侧**（多头⇒卖、空头⇒买；空仓取两侧均值）
                    if inv > 1e-9:
                        stats["S1_仅减仓侧(单边常态)"]["_all"].append(q.w_ask_bp)
                        stats["S1_仅减仓侧(单边常态)"][s].append(q.w_ask_bp)
                    elif inv < -1e-9:
                        stats["S1_仅减仓侧(单边常态)"]["_all"].append(q.w_bid_bp)
                        stats["S1_仅减仓侧(单边常态)"][s].append(q.w_bid_bp)
                    else:
                        v = (q.w_bid_bp + q.w_ask_bp) / 2
                        stats["S1_仅减仓侧(单边常态)"]["_all"].append(v)
                        stats["S1_仅减仓侧(单边常态)"][s].append(v)
                    # S4：vol_pause 拦截的快照整个不报（σ > 1.0）
                    if sigma <= 1.0:
                        stats["S4_无vol_pause拦截"]["_all"].append((q.w_bid_bp + q.w_ask_bp) / 2)
                    # 偏斜饱和程度
                    stats["_inv_abs"]["_all"].append(abs(inv))

    print(f"  快照样本 {tot_n}，冻结档占比 {100.0*frozen_n/max(1,tot_n):.1f}%"
          f"（离线独立复算，用于验证 F205b 的模型读数）")
    print(f"  |inv_ratio| 均值 {np.mean(stats['_inv_abs']['_all']):.3f} "
          f"（=1 的比例 {100*np.mean(np.array(stats['_inv_abs']['_all']) > 0.99):.1f}%）")

    print(f"\n  {'场景':<24}{'样本':>7}{'平均挂宽bp':>12}{'中位':>8}")
    order = ["S0_双侧(模型口径)", "S1_仅减仓侧(单边常态)", "S2_无偏斜", "S3_无冻结档",
             "S4_无vol_pause拦截"]
    base_v = None
    for name in order:
        v = stats.get(name, {}).get("_all") or []
        if not v:
            continue
        m = float(np.mean(v))
        if name == "S0_双侧(模型口径)":
            base_v = m
        print(f"  {name:<24}{len(v):>7}{m:>12.2f}{float(np.median(v)):>8.2f}"
              + (f"   Δvs模型 {m-base_v:+.2f}" if base_v else ""))

    print("\n【逐币（S0 双侧 / S1 仅减仓侧）】")
    for s in SYMS:
        a0 = stats.get("S0_双侧(模型口径)", {}).get(s) or []
        a1 = stats.get("S1_仅减仓侧(单边常态)", {}).get(s) or []
        if a0:
            print(f"  {s:<5} 双侧 {np.mean(a0):>6.2f}bp   减仓侧 {np.mean(a1) if a1 else float('nan'):>6.2f}bp"
                  f"   （n={len(a0)}）")

    print("\n对照：模型同口径最终挂宽 **8.36bp**（同窗口重放）｜实盘实测 **~5.1bp**（5.5/4.7）")
    print("读法：哪个场景的平均值最接近 5.1bp ⇒ 它就是实盘偏窄的主因 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
