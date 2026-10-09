# -*- coding: utf-8 -*-
"""[F223b] 单币仓位失控证据（只读，不改任何参数）：
    用 lane_ledger 逐笔重建每个币的历史仓位曲线，回答：
      ① 各币历史上最大 |qty| / |名义| 是多少？发生在什么时刻、什么事件？
      ② 那是"单边连续加仓"吗（方向是否一直不变）？
      ③ 当时（F217 前）compound_ratio=1.0 ⇒ 每腿 = 100% 权益，是仓位放大的前提。
    输出只做证据与提案，不落地任何配置 ✓。
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal  # noqa: E402

F217_TS = None  # compound_ratio 1.0→0.1 的落地时刻（用 prev_params 定位，见下）


def main() -> int:
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT ts, symbol, event, notional, price_bp, meta_json"
            " FROM lane_ledger WHERE lane_id='mm_asterdex'"
            " ORDER BY ts ASC")).mappings().all()

    # 逐币重建：qty 是**绝对值**，方向由 side 给出（已实测 meta_json 只有
    # {'fill_px','flatten','mid_px','notional','price_usd','qty','side','source'}）。
    sym = defaultdict(lambda: {"qty": 0.0, "max_qty": 0.0, "max_notional": 0.0,
                               "t_max": None, "runs": [], "n_fill": 0})
    for r in rows:
        meta = r["meta_json"] or {}
        q = float(meta.get("qty") or 0.0)
        side = str(meta.get("side") or "")
        if q == 0.0:
            continue
        signed = q if str(side).lower() in ("buy", "b") else -q
        s = sym[r["symbol"]]
        s["qty"] += signed
        s["n_fill"] += 1
        if abs(s["qty"]) > abs(s["max_qty"]):
            s["max_qty"] = s["qty"]
            s["max_notional"] = float(r["notional"] or 0.0)
            s["t_max"] = r["ts"]
            s["side_at_max"] = side

    print(f"{'币':<5}{'成交笔数':>8}{'历史最大|qty|':>16}{'时刻':>20}{'末仓':>14}")
    for k, s in sorted(sym.items()):
        t = s["t_max"].strftime("%m-%d %H:%M") if s["t_max"] else "-"
        print(f"{k:<5}{s['n_fill']:>8}{s['max_qty']:>16.4f}{t:>20}{s['qty']:>14.4f}")

    print("\n== F217 落地痕迹（evolution） ==")
    from backend.services import lane_registry as reg
    lane = reg.get_lane("mm_asterdex") or {}
    evo = (lane.get("meta") or {}).get("evolution") or {}
    print("  reason:", evo.get("reason"))
    print("  last_change_ts:", evo.get("last_change_ts"))
    print("  prev_params:", evo.get("prev_params"))

    # 最大仓对应的账本附近行情：max_qty 时刻前后该币 net_bp（逆向选择是否集中在加仓期）
    print("\n== 加仓期间 vs 平仓期间的 price_bp（逆选择）==")
    for k, s in sorted(sym.items()):
        if s["t_max"] is None:
            continue
        with SessionLocal() as db:
            pre = db.execute(text(
                "SELECT AVG(price_bp) a, SUM(notional) n FROM lane_ledger"
                " WHERE lane_id='mm_asterdex' AND symbol=:s AND ts < :t AND ts > :t - interval '60 minutes'"
            ), {"s": k, "t": s["t_max"]}).mappings().first()
            post = db.execute(text(
                "SELECT AVG(price_bp) a, SUM(notional) n FROM lane_ledger"
                " WHERE lane_id='mm_asterdex' AND symbol=:s AND ts >= :t AND ts < :t + interval '60 minutes'"
            ), {"s": k, "t": s["t_max"]}).mappings().first()
        print(f"  {k:<5} 前1h avg_price_bp={pre['a'] and round(float(pre['a']),2)}"
              f" (名义{pre['n'] and round(float(pre['n']))})"
              f" | 后1h avg_price_bp={post['a'] and round(float(post['a']),2)}"
              f" (名义{post['n'] and round(float(post['n']))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
