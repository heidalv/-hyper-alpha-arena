# -*- coding: utf-8 -*-
"""资本与边际看板聚合（v3 方向 8，dashboard）。

一次返回：KPI、三桶分配、策略/套利贡献、Agent 可信度、模型一致性、数据新鲜度、配额。
原料全部只读既有落盘与账本——本路由不造数、不改状态。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _age_h(ts_ms: Optional[float]) -> Optional[float]:
    if ts_ms is None:
        return None
    try:
        t = float(ts_ms)
        if t > 1e12:
            t = t / 1000.0
        return round((time.time() - t) / 3600.0, 2)
    except (TypeError, ValueError):
        return None


def _freshness() -> List[Dict[str, Any]]:
    items = [
        ("allocation", DATA_ROOT / "allocation" / "latest.json", "ts_ms"),
        ("scorecard", DATA_ROOT / "arb" / "scorecard_latest.json", "ts_ms"),
        ("timing_weights", DATA_ROOT / "agents" / "latest_timing_weights.json", "ts_ms"),
        ("event_study", DATA_ROOT / "event_study" / "latest.json", "ts_ms"),
        ("carry_sim", DATA_ROOT / "cashflow" / "carry_sim" / "latest.json", "ts_ms"),
        ("trend_drift", DATA_ROOT / "trend_drift" / "latest.json", "ts_ms"),
        ("paid_data", DATA_ROOT / "research" / "paid_data_decision.json", "ts_ms"),
        ("f4_gate", DATA_ROOT / "trend_e1" / "f4_gate_latest.json", "ts_ms"),
    ]
    out = []
    for name, path, key in items:
        data = _read_json(path)
        ts = None
        if data:
            ts = data.get(key) or data.get("generated_at") or data.get("ts")
        age = _age_h(ts)
        stale = age is None or age > 36
        out.append({
            "name": name, "exists": path.exists(),
            "age_h": age, "stale": stale,
            "path": str(path.relative_to(DATA_ROOT.parent)) if path.exists() else None,
        })
    return out


@router.get("/capital-margin")
def capital_margin(days: int = Query(14, ge=1, le=90)) -> Dict[str, Any]:
    """资本与边际总览。"""
    kpi: Dict[str, Any] = {}
    buckets = {}
    agents = []
    consistency = None
    quota = None
    arb = None
    e5 = []
    notes: List[str] = []

    # 分配器
    try:
        from backend.services.allocation.capital_allocator import (
            latest_allocation, allocate, promotion_frozen,
        )
        alloc = latest_allocation() or allocate(persist=False)
        buckets = alloc.get("bucket_weights") or {}
        kpi["credibility_scale"] = alloc.get("credibility_scale")
        kpi["promotion_freeze"] = promotion_frozen()
    except Exception as exc:
        notes.append(f"allocation: {exc}")

    # 套利 scorecard
    try:
        from backend.services.arbitrage.scorecard import latest_scorecard
        arb = latest_scorecard()
        if arb and arb.get("kpi"):
            kpi["arb_pnl"] = arb["kpi"].get("pnl")
            kpi["arb_ann"] = arb["kpi"].get("annualized")
            kpi["arb_mdd"] = arb["kpi"].get("max_drawdown")
            kpi["arb_occupied"] = arb["kpi"].get("occupied_usd")
    except Exception as exc:
        notes.append(f"scorecard: {exc}")

    # edge_ledger 摘要（若有）
    try:
        from backend.services.analysis import ledgers
        if hasattr(ledgers, "edge_summary"):
            kpi["edge"] = ledgers.edge_summary(days=days)
        elif hasattr(ledgers, "summary"):
            kpi["edge"] = ledgers.summary(days=days)
    except Exception as exc:
        notes.append(f"edge_ledger: {exc}")

    # Agent 可信度
    try:
        from backend.services.analysis import ledgers
        rows = ledgers.agent_credibility(days)
        agents = rows if isinstance(rows, list) else []
    except Exception as exc:
        notes.append(f"agents: {exc}")

    # 模型一致性 / 配额
    try:
        from backend.services.analysis import ledgers
        if hasattr(ledgers, "model_credibility"):
            consistency = ledgers.model_credibility(days)  # type: ignore[attr-defined]
        elif hasattr(ledgers, "list_analysis_runs"):
            runs = ledgers.list_analysis_runs(limit=20)  # type: ignore[attr-defined]
            consistency = {"recent_runs": len(runs or []), "note": "无专用一致性矩阵；展示最近 analysis_runs 数"}
    except Exception as exc:
        notes.append(f"consistency: {exc}")
    try:
        from backend.services.analysis.quota_guard import get_quota_guard
        quota = get_quota_guard().snapshot()
    except Exception as exc:
        notes.append(f"quota: {exc}")

    # E5 KPI 快照
    try:
        from backend.services.strategies.event.base import get_strategy, registered_strategies
        for sid in registered_strategies():
            s = get_strategy(sid)
            if not s:
                continue
            try:
                k = s.kpi(days=min(days, 30))
                e5.append({
                    "strategy": sid,
                    "promotion_ready": k.get("promotion_ready"),
                    "n_scored": k.get("n_scored"),
                    "net_lower_bp": k.get("net_lower_bp"),
                    "hit_rate": k.get("hit_rate"),
                })
            except Exception:
                continue
    except Exception as exc:
        notes.append(f"e5: {exc}")

    # F4 / 付费决策摘要
    f4 = _read_json(DATA_ROOT / "trend_e1" / "f4_gate_latest.json")
    paid = _read_json(DATA_ROOT / "research" / "paid_data_decision.json")

    return {
        "ts_ms": int(time.time() * 1000),
        "days": days,
        "kpi": kpi,
        "bucket_weights": buckets,
        "arb_scorecard": {"kpi": (arb or {}).get("kpi"), "gate": (arb or {}).get("promotion_gate")} if arb else None,
        "agents": agents[:20],
        "model_consistency": consistency,
        "quota": quota,
        "e5": e5,
        "f4": {"passed": (f4 or {}).get("passed"), "live_allowed": (f4 or {}).get("live_allowed")} if f4 else None,
        "paid_data_summary": (paid or {}).get("summary"),
        "freshness": _freshness(),
        "notes": notes,
    }
