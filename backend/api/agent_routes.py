# -*- coding: utf-8 -*-
"""Agent 群接口（/api/agents/*，v3 方向 3，p1-agents-a）。

只读：
  GET  /api/agents/status                 每个 Agent 的配置模式 / 生效模式 / 可信度 / 可信度门结论 / 上次运行
  GET  /api/agents/latest/{agent_id}      该 Agent 最近一次运行的完整结果（findings/predictions/advice）
  GET  /api/agents/predictions            该 Agent 的预测流水（?agent=&kind=&status=&limit=）
  GET  /api/agents/credibility            Agent × kind 可信度矩阵（含未评分/待评分计数）
  GET  /api/agents/timing/weights         Timing Agent 最新的 regime 与三桶建议（E4 消费入口，观察模式产物）

写（需 X-Risk-Token 或本机）：
  POST /api/agents/run                    {"agent": "anomaly|signal_review|timing|all", "dry_run": false, "mode": null}
  POST /api/agents/score                  立即跑一次到期评分（含 Agent kind 评估器）

说明：Phase 1 三个 Agent 的 `max_mode = advise`，任何调用方式都不会改配置、TradingState 或权重；
`mode` 参数只影响是否产出 proposed 实验卡，且仍受可信度门约束。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])


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


@router.get("/status")
def agents_status(days: int = Query(60, ge=1, le=365)) -> Dict[str, Any]:
    from backend.services.agents import agents_status as _status
    from backend.services.agents.jobs import agents_enabled, ensure_registered

    ensure_registered()
    out = _status(days)
    out["enabled"] = agents_enabled()
    return out


@router.get("/latest/{agent_id}")
def agent_latest(agent_id: str) -> Dict[str, Any]:
    from backend.services.agents import read_latest

    data = read_latest(agent_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"{agent_id} 尚无运行记录")
    return data


@router.get("/predictions")
def agent_predictions(
    agent: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    rows = ledgers.list_predictions(agent=agent, kind=kind, status=status, limit=limit)
    return {"count": len(rows), "predictions": rows}


@router.get("/credibility")
def agent_credibility(days: int = Query(60, ge=1, le=365)) -> Dict[str, Any]:
    from backend.services.agents.jobs import ensure_registered
    from backend.services.analysis import ledgers

    ensure_registered()
    from backend.services.agents.base import registered_agents

    rows = ledgers.agent_credibility(days)
    ours = {a for a in registered_agents()}
    return {
        "days": days,
        "agents": [r for r in rows if str(r.get("agent") or "") in ours],
        "models": [r for r in rows if str(r.get("agent") or "").startswith("model:")],
        "all": rows,
    }


@router.get("/timing/weights")
def timing_weights() -> Dict[str, Any]:
    from backend.services.agents import read_latest

    data = read_latest("timing_weights")
    if data is None:
        raise HTTPException(status_code=404, detail="Timing Agent 尚未产出权重建议")
    return data


@router.post("/run")
def run(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.agents.jobs import run_agent, run_all_agents

    agent = str(body.get("agent") or "all").strip()
    dry_run = bool(body.get("dry_run") or False)
    mode = body.get("mode")
    if agent in ("all", "*"):
        return run_all_agents(dry_run=dry_run)
    return run_agent(agent, dry_run=dry_run, mode=mode)


@router.post("/score")
def score(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.analysis.scheduling import score_due_with_agents

    return score_due_with_agents()
