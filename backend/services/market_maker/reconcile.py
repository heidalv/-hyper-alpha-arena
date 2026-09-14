# -*- coding: utf-8 -*-
"""[F91] 做市双账对账服务：运行态持仓 vs 账本重建持仓。

为什么需要：`lane_ledger` 是前端持仓/归因的**唯一事实源**，实盘决策用的却是
`lane_runtime_state`（运行态）。两者一旦分叉，后果是静默的——
  - 前端显示实盘并不存在的持仓（2026-09-14 实测 BTC $5.10 幽灵仓）；
  - 净敞口/方向上限的统计口径与真实风险不一致；
  - 已实现盈亏与期末库存对不上。
根因（已修 F91）：减仓腿按 dollar 腿量平仓，两腿 mid 不同 ⇒ 每次往返留 |Δqty|
残差，均值回归下两方向同号 ⇒ 单向漂移。本模块提供**持续巡检**能力：
  - `compare_lane_books()`：逐币对比（API/前端健康指标）；
  - `apply_adjustments()`：写**净额恒为 0**的对账调整行（不制造盈亏）。
口径：以运行态为准（实盘决策的真实账），调整账本使其与运行态一致。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from backend.core.tenant import system_identity
from backend.database.connection import SessionLocal
from backend.services import lane_ledger, lane_registry

logger = logging.getLogger(__name__)

TOL_QTY_REL = 1e-6      # 相对容差：0.0001% 视为浮点噪声


def lane_meta(lane_id: str) -> Dict[str, Any]:
    for ln in lane_registry.list_lanes():
        if ln.get("lane_id") == lane_id:
            return ln.get("meta") or {}
    return {}


def runtime_positions(lane_id: str) -> Dict[str, Dict[str, Any]]:
    """运行态持仓（实盘决策的真实账，来自 `lane_runtime_state`）。"""
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(text(
                "SELECT symbol, state_json, updated_ts FROM lane_runtime_state"
                " WHERE lane_id=:l ORDER BY symbol"
            ), {"l": lane_id}).mappings().all()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        st = r["state_json"] or {}
        if isinstance(st, str):
            try:
                st = json.loads(st)
            except Exception:
                st = {}
        out[str(r["symbol"])] = {
            "qty": float(st.get("qty") or 0.0),
            "avg_px": float(st.get("avg_px") or 0.0),
            "updated_ts": r["updated_ts"],
        }
    return out


def latest_marks(symbols: List[str], venue: str) -> Dict[str, float]:
    """最新中价（`market_orderbook_snapshots` 在**行情库**）。"""
    from backend.database.connection import MarketSessionLocal

    out: Dict[str, float] = {}
    with system_identity():
        with MarketSessionLocal() as db:
            for s in symbols:
                r = db.execute(text(
                    "SELECT best_bid, best_ask FROM market_orderbook_snapshots"
                    " WHERE exchange=:e AND symbol=:s AND best_bid>0 AND best_ask>best_bid"
                    " ORDER BY timestamp DESC LIMIT 1"
                ), {"e": venue, "s": s}).mappings().first()
                if r:
                    out[s] = (float(r["best_bid"]) + float(r["best_ask"])) / 2.0
    return out


def compare_lane_books(*, lane_id: str, venue: Optional[str] = None,
                       since: Optional[str] = None, days: float = 30.0,
                       symbols: Optional[List[str]] = None,
                       marks: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """逐币对比运行态与账本重建持仓。

    返回 `{ok, mismatches, rows, runtime_only, ledger_only, as_of}`；`ok=False`
    表示存在需要处理的分叉（前端应显性告警，而不是静默展示一个假持仓）。
    """
    from datetime import datetime, timezone

    meta = lane_meta(lane_id)
    venue = venue or str(meta.get("venue") or "asterdex")
    if since is None:
        since = meta.get("stats_since")
    rt = runtime_positions(lane_id)
    syms = sorted(set(symbols or []) | set(rt) | set(meta.get("symbols") or []))
    mk = marks if marks is not None else latest_marks(syms, venue)
    # [F92] 快照一致性：把账本裁到「运行态最新快照那一刻」。否则 tick 落在两次读取
    # 之间会产生假分叉（实测 ±1 条腿 ≈ ±$300，下一轮自愈）——巡检误报不可接受。
    _ts = [v.get("updated_ts") for v in rt.values() if v.get("updated_ts")]
    until = min(_ts).isoformat() if _ts else None
    led_rows = lane_ledger.open_positions(lane_id=lane_id, days=days, marks=mk,
                                          since=since, until=until)
    led = {r["symbol"]: r for r in led_rows}

    rows: List[Dict[str, Any]] = []
    mismatches: List[Dict[str, Any]] = []
    for s in syms:
        q_rt = float((rt.get(s) or {}).get("qty") or 0.0)
        q_led = float((led.get(s) or {}).get("qty") or 0.0)
        mark = float(mk.get(s) or 0.0)
        diff = q_led - q_rt
        scale = max(abs(q_rt), abs(q_led))
        bad = scale > 0 and abs(diff) / scale > TOL_QTY_REL
        row = {"symbol": s, "runtime_qty": round(q_rt, 10),
               "ledger_qty": round(q_led, 10), "diff_qty": round(diff, 10),
               "mark_px": mark, "diff_usd": round(diff * mark, 4),
               "ok": (not bad)}
        rows.append(row)
        if bad:
            mismatches.append(dict(row))
    return {
        "lane_id": lane_id, "venue": venue, "since": since,
        "ok": not mismatches,
        "checked": len(rows),
        "mismatches": mismatches,
        "rows": rows,
        "rt_only": [s for s in rt if s not in set(meta.get("symbols") or [])],
        "state_ts": until,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


def apply_adjustments(*, lane_id: str, mismatches: List[Dict[str, Any]]) -> int:
    """按对账结果写调整行（净额恒为 0，仅校正库存），返回写入条数。

    `side` = 平掉残差的方向；`fill_px = mid_px` ⇒ 价差/价格维度均为 0、费率 0
    ⇒ 六维净额 = 0，**不制造任何盈亏**，meta 标记 `source=reconcile` 可审计。
    """
    n = 0
    for m in mismatches:
        if float(m.get("mark_px") or 0.0) <= 0:
            logger.warning("[reconcile] %s 无行情价，跳过（需人工确认）", m.get("symbol"))
            continue
        side = "sell" if float(m["diff_qty"]) > 0 else "buy"
        ok = lane_ledger.record_fill(
            lane_id=lane_id, symbol=str(m["symbol"]), side=side,
            qty=abs(float(m["diff_qty"])), fill_px=float(m["mark_px"]),
            mid_px=float(m["mark_px"]), fee_rate=0.0,
            meta={"source": "reconcile", "reason": "runtime_vs_ledger_divergence",
                  "runtime_qty": m.get("runtime_qty"), "ledger_qty": m.get("ledger_qty")},
        )
        n += 1 if ok else 0
    return n
