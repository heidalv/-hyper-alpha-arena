# -*- coding: utf-8 -*-
"""双模型深度分析接口（/api/analysis/*，v3 方向 2/3/8，p0-model-gateway）。

只读：
  GET  /api/analysis/gateway/status       三条传输配置/健康 + QuotaGuard 快照
  GET  /api/analysis/quota                QuotaGuard 用量与预算
  GET  /api/analysis/runs                 analysis_runs 列表（task / transport / group / since_hours / limit）
  GET  /api/analysis/runs/{run_id}        单次 run（含 context_pack）
  GET  /api/analysis/runs/summary         近 N 天按 task×transport 的次数/成功率/成本/共识分
  GET  /api/analysis/signals              signal_ledger（source / symbol / status）
  GET  /api/analysis/signals/stats        信号源命中率 / 超额 / Brier
  GET  /api/analysis/predictions          agent_predictions
  GET  /api/analysis/predictions/credibility   Agent × kind 可信度矩阵
  GET  /api/analysis/experiments          实验卡列表
  GET  /api/analysis/experiments/stats    建议来源的采纳率（元评分）
  GET  /api/analysis/context-pack/preview 构建一份 context pack（不调模型）预览各层与 token 估算
  GET  /api/analysis/credibility/models   模型×任务可信度矩阵（日简报连续天数 / 共识分 / 信号命中）
  GET  /api/analysis/tasks/latest         最近一次日简报/周复盘/择时/事件评估落盘摘要

  GET  /api/analysis/event-study          最近一次全类型冲击报告（latest.json）；?event_type= 过滤
  GET  /api/analysis/event-study/run      只读预览不重跑；重跑用 POST /event-study/run（耗时、需 token）

写（需 X-Risk-Token 或本机；会消耗模型配额）：
  POST /api/analysis/gateway/test         {"transport": "minimax|glm_opencode|deepseek|dual", "prompt": "..."} 小样本连通性测试
  POST /api/analysis/score                立即执行一次到期评分
  POST /api/analysis/experiments          创建实验卡
  POST /api/analysis/experiments/{id}/transition   {"status": "running|evaluating|adopted|rejected|extended|rolled_back", ...}
  POST /api/analysis/tasks/daily_brief    手动跑日度简报（dry_run 可选）
  POST /api/analysis/tasks/weekly_review  手动跑周度复盘
  POST /api/analysis/tasks/timing         手动跑择时
  POST /api/analysis/tasks/event_impact   {"event_id": N, "force": false, "dry_run": false}
  POST /api/analysis/tasks/event_scan     {"limit": 3, "dry_run": false} 批量扫描评估
  POST /api/analysis/event-study/run      立刻重跑全类型或单类型冲击回测（只读库、不调模型，可能数十秒）
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


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


# ─────────────────────────── gateway / quota ───────────────────────────
@router.get("/gateway/status")
def gateway_status() -> Dict[str, Any]:
    from backend.services.analysis.model_gateway import get_model_gateway

    return get_model_gateway().status()


@router.get("/quota")
def quota() -> Dict[str, Any]:
    from backend.services.analysis.quota_guard import get_quota_guard

    return get_quota_guard().snapshot()


@router.post("/gateway/test")
def gateway_test(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """小样本连通性测试（走 QuotaGuard 的 light 预算；dual 走完整交叉验证协议）。"""
    _authorize(request, body.get("token"))
    from backend.services.analysis import schemas
    from backend.services.analysis.model_gateway import get_model_gateway

    transport = str(body.get("transport") or "dual").strip()
    prompt = str(body.get("prompt") or "基于以下事实给出你对 BTC 未来 24 小时的方向判断：BTC 24h +1.2%，资金费 0.01%/8h，OI 24h +3%，无重大事件。")
    system = "你是量化投研分析员。只依据给定事实作答，不要编造数据。\n" + schemas.output_contract("gateway_test")
    gw = get_model_gateway()
    if transport == "dual":
        res = gw.dual_call("gateway_test", system, prompt, max_output_tokens=600, timeout_s=float(body.get("timeout_s") or 180))
        return res.to_dict()
    if transport not in gw.transports:
        raise HTTPException(status_code=400, detail=f"transport 必须是 {list(gw.transports)} 或 dual")
    res = gw.call("gateway_test", system, prompt, transport=transport, max_output_tokens=600,
                  timeout_s=float(body.get("timeout_s") or 180))
    return res.to_dict()


@router.post("/tasks/trend-chart")
def trend_chart_run(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """[P3 2026-09-05] 手动触发多模态趋势图审（单币）：K线图包双票 + GLM 第三票仲裁 → 入账。

    body: {"symbol": "BTC", "dry_run": false}
    """
    _authorize(request, body.get("token"))
    from backend.services.analysis import tasks as analysis_tasks

    symbol = str(body.get("symbol") or "BTC").strip().upper()
    dry_run = bool(body.get("dry_run", False))
    timeout_hint = float(body.get("timeout_s") or 240)
    out = analysis_tasks.run_trend_chart_review(symbol, dry_run=dry_run)
    out.setdefault("timeout_hint", timeout_hint)
    return out


# ─────────────────────────── runs ───────────────────────────
@router.get("/runs/summary")
def runs_summary(days: int = Query(7, ge=1, le=90)) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    return ledgers.runs_summary(days)


@router.get("/runs")
def runs(task: Optional[str] = None, transport: Optional[str] = None, group: Optional[str] = None,
         since_hours: Optional[float] = Query(None, ge=0.01), limit: int = Query(100, ge=1, le=1000)) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    since_ms = int((time.time() - since_hours * 3600) * 1000) if since_hours else None
    rows = ledgers.list_runs(task=task, transport=transport, consensus_group=group, since_ms=since_ms, limit=limit)
    return {"count": len(rows), "runs": rows}


@router.get("/runs/{run_id}")
def run_detail(run_id: str) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    row = ledgers.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    siblings = ledgers.list_runs(consensus_group=row.get("consensus_group"), limit=10) if row.get("consensus_group") else []
    return {"run": row, "group": siblings}


# ─────────────────────────── signals / predictions ───────────────────────────
@router.get("/signals/stats")
def signals_stats(days: int = Query(30, ge=1, le=365)) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    return {"days": days, "sources": ledgers.signal_source_stats(days)}


@router.get("/signals")
def signals(source: Optional[str] = None, symbol: Optional[str] = None, status: Optional[str] = None,
            since_hours: Optional[float] = Query(None, ge=0.01), limit: int = Query(200, ge=1, le=2000)) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    since_ms = int((time.time() - since_hours * 3600) * 1000) if since_hours else None
    rows = ledgers.list_signals(source=source, symbol=symbol, status=status, since_ms=since_ms, limit=limit)
    return {"count": len(rows), "signals": rows}


@router.get("/predictions/credibility")
def predictions_credibility(days: int = Query(30, ge=1, le=365)) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    return {"days": days, "matrix": ledgers.agent_credibility(days)}


@router.get("/predictions")
def predictions(agent: Optional[str] = None, kind: Optional[str] = None, status: Optional[str] = None,
                limit: int = Query(200, ge=1, le=2000)) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    rows = ledgers.list_predictions(agent=agent, kind=kind, status=status, limit=limit)
    return {"count": len(rows), "predictions": rows}


@router.post("/score")
def score_now(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.analysis import ledgers

    return ledgers.score_due(limit=int(body.get("limit") or 500))


# ─────────────────────────── experiments ───────────────────────────
@router.get("/experiments/stats")
def experiments_stats(days: int = Query(90, ge=1, le=365)) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    return {"days": days, "sources": ledgers.experiment_source_stats(days)}


@router.get("/experiments")
def experiments(status: Optional[str] = None, source: Optional[str] = None, limit: int = Query(100, ge=1, le=1000)) -> Dict[str, Any]:
    from backend.services.analysis import ledgers

    rows = ledgers.list_experiments(status=status, source=source, limit=limit)
    return {"count": len(rows), "experiments": rows, "statuses": list(ledgers.EXPERIMENT_STATUSES)}


@router.post("/experiments")
def create_experiment(request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.analysis import ledgers
    from backend.services.analysis.context_pack import config_hash_and_flags

    try:
        cfg_hash, _ = config_hash_and_flags()
    except Exception:
        cfg_hash = None
    eid = ledgers.create_experiment(
        source=str(body.get("source") or "manual"),
        title=str(body.get("title") or ""),
        hypothesis=str(body.get("hypothesis") or ""),
        change=body.get("change") if isinstance(body.get("change"), dict) else {},
        expected_metrics=body.get("expected_metrics") if isinstance(body.get("expected_metrics"), list) else [],
        window_hours=int(body.get("window_hours") or 0),
        rollback_condition=body.get("rollback_condition"),
        config_hash_before=cfg_hash,
        notes=body.get("notes"),
    )
    if not eid:
        raise HTTPException(status_code=400, detail="实验卡字段不完整：title / hypothesis / change{} / expected_metrics[] / window_hours>0 必填")
    return {"id": eid}


@router.post("/experiments/{experiment_id}/transition")
def transition_experiment(experiment_id: str, request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.analysis import ledgers

    ok = ledgers.transition_experiment(
        experiment_id, str(body.get("status") or ""), result=body.get("result"), decision=body.get("decision"),
        decided_by=body.get("decided_by"), config_hash_after=body.get("config_hash_after"), notes=body.get("notes"),
    )
    if not ok:
        raise HTTPException(status_code=400, detail="迁移失败：id 不存在或 status 非法")
    return {"ok": True}


# ─────────────────────────── context pack preview ───────────────────────────
@router.get("/context-pack/preview")
def context_pack_preview(task: str = Query("daily_brief"), symbols: Optional[str] = None,
                         layers: Optional[str] = None, max_tokens: int = Query(40000, ge=1000, le=120000)) -> Dict[str, Any]:
    from backend.services.analysis import context_pack
    from backend.services.analysis.quota_guard import estimate_tokens

    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    lays = [s.strip() for s in layers.split(",")] if layers else None
    pack = context_pack.build(task, symbols=syms, layers=lays)
    text = pack.to_prompt_text(max_tokens)
    return {
        "hash": pack.hash, "data_cutoff_ms": pack.data_cutoff_ms, "errors": pack.errors,
        "tokens_est": estimate_tokens(text), "layers": pack.layers,
    }


# ─────────────────────────── credibility / latest / tasks ───────────────────────────
@router.get("/credibility/models")
def credibility_models(days: int = Query(30, ge=1, le=365)) -> Dict[str, Any]:
    from backend.services.analysis.tasks import model_task_credibility

    return model_task_credibility(days)


@router.get("/tasks/latest")
def tasks_latest() -> Dict[str, Any]:
    """读取 backend/data/analysis/latest_*.json（不调模型）。"""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "data" / "analysis"
    names = (
        "latest_daily_brief.json", "latest_weekly_review.json", "latest_timing.json",
        "latest_event_impact.json", "latest_bucket_weights.json", "latest_engine_capital.json",
    )
    out: Dict[str, Any] = {"dir": str(root), "items": {}}
    for name in names:
        path = root / name
        if not path.exists():
            out["items"][name] = None
            continue
        try:
            out["items"][name] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            out["items"][name] = {"error": str(exc)[:160]}
    return out


@router.post("/tasks/daily_brief")
def task_daily_brief(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.analysis.tasks import run_daily_brief

    return run_daily_brief(dry_run=bool(body.get("dry_run")))


@router.post("/tasks/weekly_review")
def task_weekly_review(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.analysis.tasks import run_weekly_review

    return run_weekly_review(dry_run=bool(body.get("dry_run")))


@router.post("/tasks/timing")
def task_timing(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.analysis.tasks import run_timing

    return run_timing(dry_run=bool(body.get("dry_run")))


@router.post("/tasks/event_impact")
def task_event_impact(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.analysis.tasks import run_event_impact

    eid = body.get("event_id")
    if eid is None and not body.get("event"):
        raise HTTPException(status_code=400, detail="需要 event_id 或 event{}")
    return run_event_impact(
        event=body.get("event") if isinstance(body.get("event"), dict) else None,
        event_id=int(eid) if eid is not None else None,
        force=bool(body.get("force")),
        dry_run=bool(body.get("dry_run")),
    )


@router.post("/tasks/event_scan")
def task_event_scan(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.services.analysis.tasks import scan_and_eval_events

    limit = body.get("limit")
    return scan_and_eval_events(
        limit=int(limit) if limit is not None else None,
        dry_run=bool(body.get("dry_run")),
    )


# ─────────────────────────── event study ───────────────────────────
@router.get("/event-study")
def event_study_latest(event_type: Optional[str] = None) -> Dict[str, Any]:
    from backend.research.event_study import latest_report

    data = latest_report()
    if not data:
        return {"ok": False, "reason": "尚未生成报告（等待 event_study_daily 或 POST /event-study/run）"}
    if event_type:
        row = (data.get("types") or {}).get(event_type)
        if row is None:
            raise HTTPException(status_code=404, detail=f"latest.json 中无 {event_type}")
        return {"ok": True, "event_type": event_type, "report": row, "ts_ms": data.get("ts_ms")}
    return {"ok": True, **{k: data[k] for k in data if k != "types"}, "types": list((data.get("types") or {}).keys())}


@router.post("/event-study/run")
def event_study_run(request: Request, body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _authorize(request, body.get("token"))
    from backend.research.event_study import run_all, run_event_study, write_reports

    et = str(body.get("event_type") or "").strip()
    kw = {}
    if body.get("min_n") is not None:
        kw["min_n"] = int(body["min_n"])
    if et:
        reports = {et: run_event_study(et, **kw)}
    else:
        reports = run_all(**kw)
    path = write_reports(reports)
    return {
        "ok": True,
        "path": str(path),
        "significant": [k for k, v in reports.items() if v.significant],
        "summary": [
            {"event_type": k, "n_used": v.n_used, "significant": v.significant,
             "hit_rate": v.hit_rate, "optimal_hold_h": v.optimal_hold_h, "mean_at_opt": v.mean_at_opt}
            for k, v in reports.items()
        ],
    }
