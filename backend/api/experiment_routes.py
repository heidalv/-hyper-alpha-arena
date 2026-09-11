# -*- coding: utf-8 -*-
"""实验卡接口（/api/experiments/*，v3 方向 3，p2-agents-b）。

只读：
  GET  /api/experiments                    实验卡列表（?status=&source=&limit=）
  GET  /api/experiments/summary            按状态/来源统计 + 采纳率 + 生命周期配置
  GET  /api/experiments/metrics            可用指标名与 scope 语法（写卡片时对照）
  GET  /api/experiments/{eid}              单卡详情（含最近一次求值明细）
  GET  /api/experiments/{eid}/evaluate     **试算**：按当前数据求值但不落状态（安全预览）

写（需 X-Risk-Token 或本机）：
  POST /api/experiments                    人工建卡（五要素齐全才收）
  POST /api/experiments/{eid}/start        proposed → running；改配置型需 force=true
  POST /api/experiments/{eid}/decide       立即求值并落终态
  POST /api/experiments/advance            手动跑一次生命周期推进

设计要点：`GET .../evaluate` 是**只读试算**，`POST .../decide` 才会写状态——想看看现在够不够
格随时可以看，但不会因为多看一眼就把卡片提前结案。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


def _authorize(request: Request, token_in_body: Optional[str] = None) -> None:
    expected = (os.getenv("RISK_COMMAND_TOKEN", "") or "").strip()
    provided = (request.headers.get("X-Risk-Token") or token_in_body or "").strip()
    if expected:
        if provided != expected:
            raise HTTPException(status_code=401, detail="invalid token")
        return
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(status_code=403, detail="RISK_COMMAND_TOKEN 未配置，仅允许本机调用")


def _get_card(eid: str) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    for c in ledgers.list_experiments(limit=1000):
        if c.get("id") == eid:
            return c
    raise HTTPException(status_code=404, detail="实验卡不存在")


@router.get("")
def list_cards(status: Optional[str] = Query(None), source: Optional[str] = Query(None),
               limit: int = Query(100, ge=1, le=1000)) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    cards = ledgers.list_experiments(status=status, source=source, limit=limit)
    return {"n": len(cards), "experiments": cards}


@router.get("/summary")
def summary(days: int = Query(90, ge=1, le=365)) -> Dict[str, Any]:
    from backend.services.experiments.lifecycle import summary as _summary

    return _summary(days)


@router.get("/metrics")
def metrics_help() -> Dict[str, Any]:
    """写卡片时对照：有哪些指标、每个指标该配什么 scope。"""
    from backend.services.experiments.metrics import METRIC_REGISTRY

    return {
        "metrics": sorted(METRIC_REGISTRY),
        "scopes": {
            "source:<name>": "signal_ledger 的信号源（E5 策略、因子等）",
            "agent:<id>": "agent_predictions 的该 Agent 平均得分",
            "exec:<account_id>": "paper_orders 的执行质量",
            "account:<id>": "edge_ledger 的持仓级净边际",
            "global": "不限定",
        },
        "ops": [">", ">=", "<", "<=", "==", "!="],
        "spec_example": {
            "metric": "excess_lower_bp", "op": ">", "threshold": 14,
            "scope": "source:e5_2_funding_shock", "min_n": 30,
            "baseline": "before（可选：改为与实验开始前等长窗口的差值）",
        },
        "verdicts": {
            "pass": "全部指标有结论且达标 → adopted",
            "fail": "有结论但至少一项不达标 → rejected",
            "inconclusive": "至少一项样本不足 → extended（继续观察，不误判为失败）",
        },
    }


@router.get("/{eid}")
def get_card(eid: str) -> Dict[str, Any]:
    return _get_card(eid)


@router.get("/{eid}/evaluate")
def preview_evaluate(eid: str) -> Dict[str, Any]:
    """只读试算：按当前数据求值，**不改状态**。"""
    import json as _json

    from backend.services.experiments.metrics import evaluate_expected_metrics
    from backend.services.experiments.lifecycle import now_ms

    card = _get_card(eid)
    specs = card.get("expected_metrics") or []
    if isinstance(specs, str):
        try:
            specs = _json.loads(specs)
        except Exception:
            specs = []
    started = int(card.get("started_ms") or card.get("created_ms") or 0)
    ends = int(card.get("ends_ms") or now_ms())
    ev = evaluate_expected_metrics(specs, since_ms=started, until_ms=min(ends, now_ms()))
    return {"id": eid, "status": card.get("status"), "preview": True,
            "window": {"since_ms": started, "until_ms": min(ends, now_ms())}, **ev}


@router.post("")
def create_card(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _authorize(request, payload.get("token"))
    from backend.services.analysis import ledgers

    eid = ledgers.create_experiment(
        source=str(payload.get("source") or "manual"),
        title=str(payload.get("title") or ""),
        hypothesis=str(payload.get("hypothesis") or ""),
        change=payload.get("change") or {},
        expected_metrics=payload.get("expected_metrics") or [],
        window_hours=int(payload.get("window_hours") or 0),
        rollback_condition=payload.get("rollback_condition"),
        notes=payload.get("notes"),
    )
    if not eid:
        raise HTTPException(status_code=400,
                            detail="五要素不全：title / hypothesis / change / expected_metrics / window_hours 都必须给")
    return {"ok": True, "id": eid, "status": "proposed"}


@router.post("/{eid}/start")
def start_card(eid: str, request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, payload.get("token"))
    from backend.services.experiments.lifecycle import start_experiment

    res = start_experiment(eid, by=str(payload.get("by") or "manual"), force=bool(payload.get("force")))
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason") or "无法开始")
    return res


@router.post("/{eid}/decide")
def decide_card(eid: str, request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, payload.get("token"))
    from backend.services.experiments.lifecycle import evaluate_experiment

    res = evaluate_experiment(eid, by=str(payload.get("by") or "manual"))
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason") or "求值失败")
    return res


@router.post("/advance")
def advance(request: Request, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, payload.get("token"))
    from backend.services.experiments.lifecycle import advance_experiments

    return advance_experiments(auto_start=payload.get("auto_start"))
