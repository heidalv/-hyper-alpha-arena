"""离线判定：**"被判定区间"取 1 个桶还是多个桶**，能否解释实盘/模型 4.2 倍的成交笔数差。

[F209 2026-09-15] 要回答的问题
--------------------------------------------------
同窗口(12:57~14:02)、同配置、挂宽可比（模型 4.26bp vs 实盘 5.10bp）下：
    模型 48 次穿越 → 46 笔成交（转化率 96% ✓）
    实盘 203 笔成交（转化率 ~100%，F105 ✓）
⇒ 实盘看到的穿越次数约为模型的 **4.2 倍** ✗。
两侧的引擎逻辑相同（都走 `plan_tick`，判据一致 ✓），差异只在**被判定区间装了什么**：
    实盘(runner.py:1264-1277): `WHERE timestamp > :lo`（水位可能滞留多桶）
                              + `FILTER (timestamp + 15s > :qts)`（按被判定挂单时刻裁剪）
                              ⇒ 实际覆盖"挂单那一刻之后的所有桶"⇒ 约 3 个桶（15s×3）
    模型(portfolio_replay)   : `seg_slice(watermark, snap)` ⇒ 通常 **1 个桶**
本脚本直接用行情算：**同一张挂单（同一挂宽、同一时刻）在不同长度的区间里，被触及的概率差多少**
⇒ 若 3 桶 ≈ 4× 1 桶，则区间定义**就是**根因 ✓（且转换率两侧都≈100% ⇒ 穿越数≈成交数 ✓）。

口径：挂宽固定 w（默认 5.0bp = 实盘实测），买价 = mid×(1−w/1e4)、卖价 = mid×(1+w/1e4)；
触及判据与 `plan_tick` 一致：**该侧有主动量 且 区间价穿过挂单价**
（买腿需 `low < bid` 且当段有主动卖量；卖腿需 `high > ask` 且当段有主动买量 ✓）。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.database.connection import MarketSessionLocal  # noqa: E402

SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL"]
TZ = timezone(timedelta(hours=8))
SEG = 15_000


def main() -> int:
    a_s = sys.argv[1] if len(sys.argv) > 1 else "2026-09-15T12:57:00+08:00"
    b_s = sys.argv[2] if len(sys.argv) > 2 else "2026-09-15T14:02:00+08:00"
    w = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
    a = datetime.fromisoformat(a_s)
    b = datetime.fromisoformat(b_s)
    print(f"剔除区间长度效应检验 · {a_s[11:16]}~{b_s[11:16]} · 挂宽 {w}bp（实盘实测值）")
    print("对照：实盘 203 笔 / 模型 46 笔（同窗口同挂宽量级）\n")

    tot: Dict[int, int] = {k: 0 for k in (1, 2, 3, 4, 6)}
    tot_dec = 0
    print(f"  {'币':<6}{'决策数':>8}" + "".join(f"{str(k)+'桶':>8}" for k in sorted(tot)))
    for s in SYMS:
        with MarketSessionLocal() as ses:
            ses.execute(text("SET statement_timeout = 60000"))
            ob = [dict(r) for r in ses.execute(text(
                """SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots
                   WHERE symbol=:s AND timestamp >= :a AND timestamp <= :b
                   ORDER BY timestamp"""), {"s": s, "a": int(a.timestamp() * 1000),
                                            "b": int(b.timestamp() * 1000)}).mappings().all()]
            tr = [dict(r) for r in ses.execute(text(
                """SELECT timestamp, low_price, high_price, taker_sell_volume, taker_buy_volume
                   FROM market_trades_aggregated
                   WHERE exchange='asterdex' AND symbol=:s
                     AND timestamp >= :a AND timestamp <= :b ORDER BY timestamp"""),
                {"s": s, "a": int(a.timestamp() * 1000) - SEG,
                 "b": int(b.timestamp() * 1000) + 8 * SEG}).mappings().all()]
        if len(ob) < 50 or len(tr) < 20:
            print(f"  {s:<6} 数据不足（快照 {len(ob)} / 桶 {len(tr)}）")
            continue
        tb: Dict[int, Tuple[float, float, float, float]] = {}
        for r in tr:
            lab = (int(r["timestamp"]) // SEG) * SEG
            lo = float(r["low_price"] or 0.0)
            hi = float(r["high_price"] or 0.0)
            sv = float(r["taker_sell_volume"] or 0.0)
            bv = float(r["taker_buy_volume"] or 0.0)
            if lab in tb:      # 同标签多行（写入批次）⇒ 合并
                pl, ph, ps, pb = tb[lab]
                tb[lab] = (min(pl, lo) if pl else lo, max(ph, hi),
                           ps + sv, pb + bv)
            else:
                tb[lab] = (lo, hi, sv, bv)
        labels = sorted(tb)
        hits = {k: 0 for k in tot}
        ndec = 0
        for r in ob:
            t = (int(r["timestamp"]) // SEG) * SEG
            if not r["best_bid"] or not r["best_ask"]:
                continue
            mid = (float(r["best_bid"]) + float(r["best_ask"])) / 2
            bid, ask = mid * (1 - w / 1e4), mid * (1 + w / 1e4)
            ndec += 1
            for k in sorted(tot):
                hit = False
                for j in range(1, k + 1):
                    lab = t + j * SEG
                    if lab not in tb:
                        continue
                    lo, hi, sv, bv = tb[lab]
                    if (sv > 0 and lo > 0 and lo < bid) or (bv > 0 and hi > ask):
                        hit = True
                        break
                if hit:
                    hits[k] += 1
        tot_dec += ndec
        for k in sorted(tot):
            tot[k] += hits[k]
        print(f"  {s:<6}{ndec:>8}" + "".join(f"{hits[k]:>8}" for k in sorted(tot)))

    print(f"  {'合计':<6}{tot_dec:>8}" + "".join(f"{tot[k]:>8}" for k in sorted(tot)))
    print(f"\n  {'相对 1 桶':<14}" + "".join(
        f"{tot[k]/max(1,tot[1]):>7.2f}×" for k in sorted(tot)))
    print("\n读法：若『3 桶』约为『1 桶』的 4 倍 ⇒ 区间定义就是实盘/模型 4.2 倍差异的根因 ✓"
          "（因为两侧转换率都≈100%，穿越数≈成交数）")
    print("     若各桶数差别不大 ⇒ 区间不是根因 ✗，需查量聚合（seg 主动量口径）或判定时点")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
