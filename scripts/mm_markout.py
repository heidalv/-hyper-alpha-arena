# -*- coding: utf-8 -*-
"""[F270] markout 观测台（降级版）：被动成交的 markout 分布。

调研（HFT-ML 报告 §8.3 第 1 步）要求的核心工具：不是看"已实现盈亏"，而是看
**每笔被动成交之后价格往哪走**——正 markout = 成交价优于后续中价（真 edge），
负 markout = 我们被逆向选择（成交后价格继续朝不利方向走）。

数据粒度诚实说明：本项目行情为 **30s 快照**（无逐笔/1s），因此 τ 只能取
快照网格：{30s, 90s, 300s}（1/3/10 档），无法做 5s markout ✗（报告要求的
5s 档需 1s 数据，列为数据层待办）。

markout 定义（与做市文献一致，bp）：
    买单：markout(τ) = (mid(t+τ) − fill_px) / fill_px × 1e4
    卖单：markout(τ) = (fill_px − mid(t+τ)) / fill_px × 1e4
正 = 有利（买在低位/卖在高位，随后价格朝我方走）。

用法：python scripts/mm_markout.py [--since ISO] [--lane mm_asterdex]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from backend.core.tenant import system_identity  # noqa: E402
from backend.database.connection import MarketSessionLocal, SessionLocal  # noqa: E402

TAUS_MS = [30_000, 90_000, 300_000]
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "logs", "mm_markout.jsonl")


def _mids(symbol: str, lo_ms: int, hi_ms: int) -> List[tuple]:
    with system_identity():
        with MarketSessionLocal() as db:
            rows = db.execute(text(
                "SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots "
                "WHERE exchange='asterdex' AND symbol=:s AND timestamp BETWEEN :a AND :b "
                "AND best_bid>0 AND best_ask>best_bid ORDER BY timestamp"
            ), {"s": symbol, "a": lo_ms, "b": hi_ms}).mappings().all()
    return [(int(r["timestamp"]), (float(r["best_bid"]) + float(r["best_ask"])) / 2)
            for r in rows]


def _mid_at(series: List[tuple], ts_ms: int, tol_ms: int = 45_000) -> Optional[float]:
    """取 ts 之后（含）最近的快照中价；容差内找不到返回 None。"""
    for t, m in series:
        if t >= ts_ms:
            return m if t - ts_ms <= tol_ms else None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="mm_asterdex")
    ap.add_argument("--since", default="2026-09-15T00:00:00+08:00")
    args = ap.parse_args()

    since_ms = int(datetime.fromisoformat(args.since).timestamp() * 1000)
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(text(
                "SELECT ts, symbol, meta_json FROM lane_ledger "
                "WHERE lane_id=:l AND event='fill' AND ts >= :s "
                "AND COALESCE((meta_json->>'source'),'') <> 'reconcile' "
                "ORDER BY ts"
            ), {"l": args.lane, "s": args.since}).mappings().all()

    fills = []
    for r in rows:
        m = r["meta_json"] or {}
        if isinstance(m, str):
            m = json.loads(m)
        qty = float(m.get("qty") or 0)
        px = float(m.get("fill_px") or 0)
        side = str(m.get("side") or "").lower()
        if qty <= 0 or px <= 0 or side not in ("buy", "sell"):
            continue
        fills.append({
            "ts_ms": int(r["ts"].timestamp() * 1000), "symbol": r["symbol"],
            "side": side, "qty": qty, "px": px,
            "flatten": bool(m.get("flatten")),
            "hour_cn": r["ts"].astimezone().strftime("%H"),
        })

    if not fills:
        print("无成交样本")
        return 0

    # 一次性取每个 symbol 的中价序列（覆盖 最早成交 → 最晚成交+τ）
    lo = min(f["ts_ms"] for f in fills) - 60_000
    hi = max(f["ts_ms"] for f in fills) + max(TAUS_MS) + 120_000
    series = {s: _mids(s, lo, hi) for s in sorted({f["symbol"] for f in fills})}
    print(f"样本 {len(fills)} 笔；中价序列: "
          + ", ".join(f"{s}={len(v)}" for s, v in series.items()))

    for f in fills:
        ser = series.get(f["symbol"]) or []
        out = {}
        for tau in TAUS_MS:
            mid_t = _mid_at(ser, f["ts_ms"] + tau)
            if mid_t is None:
                out[tau] = None
                continue
            mv = (mid_t - f["px"]) if f["side"] == "buy" else (f["px"] - mid_t)
            out[tau] = mv / f["px"] * 1e4
        f["markout"] = out

    def _stats(vals: List[float]) -> Dict[str, Any]:
        if not vals:
            return {"n": 0}
        vals = sorted(vals)
        n = len(vals)
        return {
            "n": n, "mean": round(sum(vals) / n, 3),
            "p05": round(vals[max(0, int(n * 0.05))], 3),
            "p50": round(vals[n // 2], 3),
            "p95": round(vals[min(n - 1, int(n * 0.95))], 3),
            "pos_share": round(sum(1 for v in vals if v > 0) / n, 3),
        }

    summary: Dict[str, Any] = {"lane": args.lane, "since": args.since,
                               "n_fills": len(fills), "by_tau": {}, "by_group": {}}
    print("\n== 全样本 markout（bp，正=有利） ==")
    for tau in TAUS_MS:
        vals = [f["markout"][tau] for f in fills if f["markout"][tau] is not None]
        st = _stats(vals)
        summary["by_tau"][f"{tau // 1000}s"] = st
        print(f"  τ={tau // 1000:>3}s  n={st.get('n')}  mean={st.get('mean')}  "
              f"p05={st.get('p05')}  p50={st.get('p50')}  p95={st.get('p95')}  "
              f"正比例={st.get('pos_share')}")

    print("\n== 分组（τ=90s） ==")
    tau0 = 90_000
    for key, sel in (
        ("入场(maker)", lambda f: not f["flatten"]),
        ("平仓(flatten)", lambda f: f["flatten"]),
    ):
        vals = [f["markout"][tau0] for f in fills if sel(f) and f["markout"][tau0] is not None]
        st = _stats(vals)
        summary["by_group"][key] = st
        print(f"  {key:<14} n={st.get('n')}  mean={st.get('mean')}  p50={st.get('p50')}")

    sym_groups: Dict[str, List[float]] = defaultdict(list)
    for f in fills:
        if f["markout"][tau0] is not None:
            sym_groups[f["symbol"]].append(f["markout"][tau0])
    for s, vals in sorted(sym_groups.items()):
        st = _stats(vals)
        summary["by_group"][f"symbol:{s}"] = st
        print(f"  {s:<14} n={st.get('n')}  mean={st.get('mean')}  p50={st.get('p50')}")

    hour_groups: Dict[str, List[float]] = defaultdict(list)
    for f in fills:
        if f["markout"][tau0] is not None:
            hour_groups[f["hour_cn"]].append(f["markout"][tau0])
    print("  按小时(北京): "
          + "  ".join(f"{h}h:{_stats(v).get('mean')}(n={len(v)})"
                      for h, v in sorted(hour_groups.items())))

    summary["ts"] = datetime.now(timezone.utc).astimezone().isoformat()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print(f"\n已追加 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
