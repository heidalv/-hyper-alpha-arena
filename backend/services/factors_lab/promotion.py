# -*- coding: utf-8 -*-
"""factors_lab → 生产晋级桥（REV-P10 · 辩论式评审 + 人工确认）。

纪律（集成分析 §3.4 / 统一策略设计）：
    - lab 的 pass 候选不自动进实盘；唯一通路 = 本模块的评审门：
      ① LLM 对抗评审（正方=因子辩护人持假设卡+回测证据；反方=审计人持过拟合/
        拥挤/容量质疑）→ ② admin 人工确认（API 显式调用）→ ③ 注册
      custom_factor_store（source=agent_lab，status=candidate）→ 走既有
      score_formula 门禁与 lifecycle 状态机（DRAFT→…→REJECTED，状态机零改动）。
    - 评审记录全程落盘（promotion_reviews.jsonl），evidence_refs 锚定。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from backend.services.factors_lab import agent4_backtester, common, config

logger = logging.getLogger(__name__)

_REVIEW_PATH = config.data_dir() / "promotion_reviews.jsonl"


_DEFENDANT_SYSTEM = (
    "你是因子晋升评审的【正方·因子辩护人】。基于给定证据（假设、公式、单测、回测指标、"
    "对齐打分）给出最强辩护：经济逻辑为何成立、指标为何可信。"
    '输出 JSON：{"score": 0-10, "arguments": ["论点1", "论点2"]}。只输出 JSON。'
)
_AUDITOR_SYSTEM = (
    "你是因子晋升评审的【反方·审计人】。对给定因子提出最尖锐的质疑：过拟合风险"
    "（样本内嫌疑）、拥挤度（与常见因子族的相似性）、容量与换手、机制的时变性。"
    '输出 JSON：{"score": 0-10, "arguments": ["质疑1", "质疑2"]}。score 越高=反对越强。只输出 JSON。'
)
_JUDGE_RULE = "正方分 ≥ 反方分 + 2 且正方分 ≥ 6 → recommend=approve；否则 reject"


def _load_candidate(expr_id: str) -> Optional[Dict]:
    for c in common.read_jsonl(config.candidates_path()):
        if c.get("expr_id") == expr_id and c.get("verdict") == "pass":
            return c
    return None


def _load_metrics(expr_id: str) -> Dict:
    for rp in sorted(config.reports_dir().glob("round_*.json")):
        try:
            rep = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in rep.get("results") or []:
            if row.get("expr_id") == expr_id:
                return {k: row.get(k) for k in (
                    "mean_rank_ic", "icir", "quantile_spread", "turnover",
                    "ic_half_life_bars", "note")}
    return {}


def debate_review(expr_id: str) -> Dict[str, object]:
    """对 lab pass 候选跑正反方辩论评审（不注册，只出结论+落盘）。"""
    cand = _load_candidate(expr_id)
    if not cand:
        return {"ok": False, "error": f"候选 {expr_id} 不存在或非 pass"}
    hyp = {}
    for h in common.read_jsonl(config.hypotheses_path()):
        if h.get("hyp_id") == cand.get("hyp_id"):
            hyp = h
            break
    metrics = _load_metrics(expr_id)
    evidence = json.dumps({
        "hypothesis": hyp.get("hypothesis"), "argument": hyp.get("argument"),
        "spec": hyp.get("spec"), "expected_ic_sign": hyp.get("expected_ic_sign"),
        "formula_note": cand.get("note"),
        "unit_tests": cand.get("unit_tests"), "alignment": cand.get("alignment"),
        "ast_sim_pool": cand.get("ast_sim_pool"),
        "backtest": metrics,
    }, ensure_ascii=False, default=str)[:2200]

    pro = common.parse_json_block(common.call_llm(
        _DEFENDANT_SYSTEM, evidence, caller="factors_lab_rev_pro", max_tokens=700) or "")
    con = common.parse_json_block(common.call_llm(
        _AUDITOR_SYSTEM, evidence, caller="factors_lab_rev_con", max_tokens=700) or "")
    pro_score = float((pro or {}).get("score") or 0)
    con_score = float((con or {}).get("score") or 0)
    recommend = "approve" if (pro_score >= con_score + 2 and pro_score >= 6) else "reject"

    review = {
        "ts": time.time(), "expr_id": expr_id, "cand_id": cand.get("cand_id"),
        "pro": {"score": pro_score, "arguments": (pro or {}).get("arguments") or []},
        "con": {"score": con_score, "arguments": (con or {}).get("arguments") or []},
        "rule": _JUDGE_RULE, "recommend": recommend,
        "evidence_refs": ["candidates.jsonl", "round_reports"],
    }
    common.append_jsonl(_REVIEW_PATH, review)
    logger.info("[FactorsLab·REV] %s 辩论评审: pro=%.0f con=%.0f → %s",
                expr_id, pro_score, con_score, recommend)
    return review


def _ast_to_formula_string(ast: Optional[dict]) -> str:
    """AST → formula_ops 风格公式串（注册 custom_factor_store 用，受限 eval 兼容）。"""
    from backend.services.factors_lab.agent3_engineer import ast_to_formula
    return ast_to_formula(ast) if isinstance(ast, dict) else ""


def promote(expr_id: str, *, reviewer: str = "admin") -> Dict[str, object]:
    """人工确认晋级：注册进 custom_factor_store（source=agent_lab, candidate）。

    前置：存在 recommend=approve 的辩论评审记录。注册后走既有
    score_formula 门禁/lifecycle，不直接进实盘信号。
    """
    approved = any(
        (r.get("expr_id") == expr_id and r.get("recommend") == "approve")
        for r in common.read_jsonl(_REVIEW_PATH))
    if not approved:
        return {"ok": False, "error": "无 approve 评审记录（先 POST /promotion/review）"}
    cand = _load_candidate(expr_id)
    if not cand:
        return {"ok": False, "error": "候选不存在或非 pass"}
    formula = _ast_to_formula_string(cand.get("ast"))
    if not formula:
        return {"ok": False, "error": "AST 转公式失败"}
    try:
        from backend.services.coin_select_platform_service import resolve_admin_tenant_id
        from backend.services.factor_engine.custom_factor_store import custom_factor_store

        name = f"agent_lab_{expr_id[:8]}"
        res = custom_factor_store.register(
            name, formula, category="agent_lab", source="agent_lab",
            extra={
                "horizon": "midlong", "timeframe": "1d",
                "note": str(cand.get("note") or "")[:60],
                "lab_expr_id": expr_id, "hyp_id": cand.get("hyp_id"),
                "expected_ic_sign": int((cand.get("alignment") or {}).get("c1", 0.5) >= 0.5 and True),
                "promoted_by": reviewer, "promoted_at": time.time(),
            },
            tenant_id=resolve_admin_tenant_id(),
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"注册失败: {str(e)[:120]}"}
    common.append_jsonl(_REVIEW_PATH, {
        "ts": time.time(), "expr_id": expr_id, "event": "promoted",
        "register_result": {k: res.get(k) for k in ("ok", "factor_id", "reason")},
        "reviewer": reviewer})
    logger.info("[FactorsLab·REV] %s 已晋级注册（%s）", expr_id, res.get("factor_id"))
    return {"ok": bool(res.get("ok")), "register": res, "formula": formula}


def review_history(limit: int = 20) -> List[Dict]:
    return common.read_jsonl(_REVIEW_PATH)[-limit:]
