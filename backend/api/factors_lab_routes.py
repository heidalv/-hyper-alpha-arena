# -*- coding: utf-8 -*-
"""factors_lab API — /api/factors-lab/*（admin 门控）。

端点：feed（人工投喂）/ scan（arXiv 监控）/ round（触发一轮）/ status /
report（最新轮报告）/ calibrate（WorldQuant 101 金样本回归）/ diversity（多样性体检）。
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/factors-lab", tags=["factors-lab"])


def _is_admin(request: Request) -> bool:
    try:
        from backend.core.request_identity import current_role
        return current_role(request) == "admin"
    except Exception:
        return False


class FeedBody(BaseModel):
    title: str
    abstract: str
    source: str = "manual"
    url: str = ""


class RoundBody(BaseModel):
    scan: bool = True


@router.post("/feed")
def feed(body: FeedBody, request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.factors_lab import agent1_literature
    return agent1_literature.feed(body.title, body.abstract, body.source, body.url)


@router.post("/scan")
def scan(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.factors_lab import agent1_literature
    return agent1_literature.scan_arxiv()


@router.post("/round")
def round_(body: RoundBody, request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.factors_lab import loop
    return loop.run_round(scan=body.scan, force=True)


@router.get("/status")
def status(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.factors_lab import loop
    return loop.status()


@router.get("/report")
def report(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    try:
        from backend.services.factors_lab import config
        reports = sorted(config.reports_dir().glob("round_*.json"))
        if not reports:
            return {"ok": False, "error": "no_reports"}
        return json.loads(reports[-1].read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(500, str(e)[:120])


@router.post("/calibrate")
def calibrate(request: Request, rebuild_golden: bool = False):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.factors_lab import calibration
    return calibration.run_calibration(rebuild_golden=rebuild_golden)


@router.get("/diversity")
def diversity(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.factors_lab import agent5_feedback
    return agent5_feedback.diversity_report()


@router.get("/config")
def lab_config(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.factors_lab import config as cfg
    return {
        "mode_enabled": cfg.enabled(),
        "panel_period": cfg.PANEL_PERIOD,
        "budget": {
            "max_hypotheses": cfg.max_hypotheses(),
            "candidates_per_hypothesis": cfg.candidates_per_hypothesis(),
            "universe_n": cfg.universe_n(),
            "round_timeout_sec": cfg.round_timeout_sec(),
        },
        "gates": {
            "diversity_jaccard": cfg.diversity_token_threshold(),
            "ast_sim": cfg.ast_sim_threshold(),
            "ic_pass": cfg.ic_pass_threshold(),
            "crowded_corr": cfg.crowded_corr_threshold(),
        },
        "cron": {
            "round_daily": "05:40 (factors_lab_round_daily)",
            "calibration_weekly": "周日 05:20 (factors_lab_calibration_weekly)",
        },
        "sources": [
            {"id": "arxiv_qfin", "kind": "自动监控", "status": "active",
             "url": "https://export.arxiv.org/api/query (cat:q-fin.TR / q-fin.PM)",
             "note": "按提交时间倒序抓最新 N 篇，LLM 抽知识卡；偶发 406 时优雅降级"},
            {"id": "manual", "kind": "人工投喂", "status": "active",
             "url": "POST /api/factors-lab/feed（标题+摘要+来源+URL）",
             "note": "任何论文/研报摘要均可投喂；去重按标题哈希"},
            {"id": "ssrn", "kind": "自动监控", "status": "planned",
             "url": "SSRN q-fin RSS", "note": "无公开 API，需 RSS 解析（后置）"},
            {"id": "wq_brain", "kind": "自动监控", "status": "planned",
             "url": "WorldQuant BRAIN 社区公开帖", "note": "需登录态/爬虫（后置，版权纪律评估）"},
        ],
        "llm": {"caller_prefix": "factors_lab_1..5", "memory": "v7_lessons 共享教训池"},
    }


@router.get("/library")
def lab_library(request: Request, limit: int = 100):
    """因子库全貌：候选(因子) × 假设 × 轮报告回测指标 合并视图。"""
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    import time as _time

    from backend.services.factors_lab import agent3_engineer, common, config

    hyps = {h.get("hyp_id"): h for h in common.read_jsonl(config.hypotheses_path())}
    # 轮报告 → {cand_id: 回测指标}（候选记录本身不含回测指标）
    metrics: dict = {}
    try:
        for rp in sorted(config.reports_dir().glob("round_*.json"))[-30:]:
            try:
                rep = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:
                continue
            for row in rep.get("results") or []:
                cid = row.get("cand_id")
                if cid and cid not in metrics:
                    metrics[cid] = {k: row.get(k) for k in (
                        "mean_rank_ic", "icir", "quantile_spread", "turnover",
                        "ic_half_life_bars", "ok", "unit", "verdict")}
                    metrics[cid]["round_ts"] = rep.get("round_ts")
    except Exception:
        pass

    cands = common.read_jsonl(config.candidates_path())[-limit:]
    factors = []
    for c in reversed(cands):  # 新在前
        m = metrics.get(c.get("cand_id")) or {}
        hyp = hyps.get(c.get("hyp_id")) or {}
        verdict = c.get("verdict") or (m.get("verdict") or {}).get("mode") if isinstance(m.get("verdict"), dict) else c.get("verdict")
        factors.append({
            "cand_id": c.get("cand_id"), "expr_id": c.get("expr_id"),
            "formula": agent3_engineer.ast_to_formula(c.get("ast")) if c.get("ast") else None,
            "ast": c.get("ast"), "note": c.get("note"),
            "hyp_id": c.get("hyp_id"),
            "hypothesis": str(hyp.get("hypothesis") or "")[:120],
            "expected_ic_sign": hyp.get("expected_ic_sign"),
            "unit_tests": c.get("unit_tests"), "unit_status": c.get("status"),
            "alignment": c.get("alignment"),
            "ast_sim_pool": c.get("ast_sim_pool"),
            "backtest": m or None,
            "verdict": verdict or c.get("mode"),
            "applied": "lab_pass_待REV-P10/P12评审" if verdict == "pass" else (
                       "已入生产池" if _in_production(c.get("expr_id")) else None),
            "ts": c.get("ts"),
            "ts_ago_min": round((_time.time() - float(c.get("ts") or 0)) / 60, 1),
        })

    knowledge = [
        {"title": k.get("title"), "source": k.get("source"), "url": k.get("url"),
         "extract_ok": k.get("extract_ok"), "card": k.get("card"), "ts": k.get("ts")}
        for k in reversed(common.read_jsonl(config.knowledge_path())[-limit:])
    ]
    hypotheses = [
        {"hyp_id": h.get("hyp_id"), "hypothesis": h.get("hypothesis"),
         "argument": h.get("argument"), "spec": h.get("spec"),
         "observation": h.get("observation"), "knowledge_ref": h.get("knowledge_ref"),
         "expected_ic_sign": h.get("expected_ic_sign"),
         "applicable_regime": h.get("applicable_regime"),
         "diversity_note": h.get("diversity_note"),
         "max_recent_jaccard": h.get("max_recent_jaccard"),
         "outcome": h.get("outcome"), "ts": h.get("ts")}
        for h in reversed(common.read_jsonl(config.hypotheses_path())[-limit:])
    ]
    return {"factors": factors, "hypotheses": hypotheses, "knowledge": knowledge,
            "counts": {"factors": len(factors), "hypotheses": len(hypotheses),
                       "knowledge": len(knowledge)}}


def _in_production(expr_id: str) -> bool:
    """是否已注册进生产因子目录（晋级后为真；当前隔离纪律下恒假，留晋级通路）。"""
    return False


# ==================== 统一策略（因子 × LLM alpha · 周外循环） ====================

@router.get("/unified-strategy/report")
def unified_report(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.unified_strategy import weekly_loop
    return weekly_loop.latest()


@router.post("/unified-strategy/run")
def unified_run(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.unified_strategy import weekly_loop
    return weekly_loop.run_weekly()


# ==================== 晋级桥（REV-P10 · 辩论评审 + 人工确认） ====================

class PromoteBody(BaseModel):
    expr_id: str


@router.get("/promotion/list")
def promotion_list(request: Request, limit: int = 20):
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.factors_lab import promotion
    return {"reviews": promotion.review_history(limit)}


@router.post("/promotion/review")
def promotion_review(body: PromoteBody, request: Request):
    """对 lab pass 候选跑正反方辩论评审（不注册）。"""
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.factors_lab import promotion
    return promotion.debate_review(body.expr_id)


@router.post("/promotion/promote")
def promotion_promote(body: PromoteBody, request: Request):
    """人工确认晋级：注册 custom_factor_store(source=agent_lab) 走既有门禁。"""
    if not _is_admin(request):
        raise HTTPException(403, "admin only")
    from backend.services.factors_lab import promotion
    return promotion.promote(body.expr_id)
