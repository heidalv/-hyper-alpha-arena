"""自动阈值调优（升级计划 v3.0 S3/R5 · 对标 Freqtrade hyperopt / Two Sigma SigOpt）。

框架：阈值向量 → 评价函数（回放/回测）→ 网格/贝叶斯搜索 → 报告落盘 →
shadow 灰度（人工审批后才可 env 生效）。**调优结果绝不自动改阈值**。

V1 域：
- "long_rule"：L1 阈值 × 前瞻网格（复用 long_rule_validator 同口径诊断，
  评价 = OOS Sharpe，附 DSR/PBO）——已可用；
- "scalp_router"：需 scalp 决策的因子分数快照落库（当前未落），登记为
  TODO，接口已留（evaluate 返回 gap 说明，不产生伪结果）。

产出：data/threshold_tune_report.json（报告）+ data/threshold_tune_approval.json（审批态）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_REPORT_PATH = os.path.join("data", "threshold_tune_report.json")
_APPROVAL_PATH = os.path.join("data", "threshold_tune_approval.json")


def _evaluate_long_rule() -> Dict[str, Any]:
    """评价 = 长线入场规则网格（M5 同口径），目标 = 最高 OOS Sharpe 且有 DSR 支持。"""
    from backend.services.factor_engine.long_rule_validator import validate_entry_rule
    rep = validate_entry_rule(symbols=["BTC", "ETH", "SOL"])
    rows = rep.get("results") or []
    best = max(rows, key=lambda r: r["oos_sharpe"]) if rows else None
    return {
        "domain": "long_rule",
        "n_combos": len(rows),
        "best": best,
        "current_l1_threshold": _env_int("LONG_V2_L1_UP_SCORE", 3),
        "recommendation": (
            f"L1={best['l1_threshold']}, fwd={best['fwd_bars']} (OOS Sharpe={best['oos_sharpe']})"
            if best else "无有效组合"
        ),
        "note": "Chandelier ATR 倍数/金字塔 R 属持仓管理参数，需持仓模拟器（V2）",
    }


def _evaluate_scalp_router() -> Dict[str, Any]:
    """TODO：scalp 路由 conf/exec 阈值的回放调优 —— 需要每次决策的因子分数快照落库。
    当前 scalp_factor_router 只落拦截统计与成交，分数未持久化 → 回放无数据源。"""
    return {
        "domain": "scalp_router",
        "status": "gap",
        "note": "需先让 scalp_factor_router 把 (score, conf, exec, outcome) 快照写入 trade_facts/决策日志；"
                "接口已留，数据就绪后按同一框架实现 evaluate。",
    }


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(os.environ.get(key, default)))
    except Exception:
        return default


_DOMAINS: Dict[str, Callable[[], Dict[str, Any]]] = {
    "long_rule": _evaluate_long_rule,
    "scalp_router": _evaluate_scalp_router,
}


def run_tune(domains: Optional[List[str]] = None) -> Dict[str, Any]:
    domains = domains or list(_DOMAINS.keys())
    report: Dict[str, Any] = {"updated_at": time.time(), "domains": {}}
    for d in domains:
        fn = _DOMAINS.get(d)
        if fn is None:
            continue
        try:
            report["domains"][d] = fn()
        except Exception as e:
            report["domains"][d] = {"domain": d, "status": "error", "error": str(e)[:200]}
            logger.warning("[ThresholdTuner] %s 调优失败: %s", d, e)
    try:
        with open(_REPORT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info("[ThresholdTuner] 报告写入 %s", _REPORT_PATH)
    except Exception as e:
        logger.warning("[ThresholdTuner] 报告落盘失败: %s", e)
    return report


def approval_state() -> Dict[str, Any]:
    """审批态（shadow）：人工确认前调优结果不生效。"""
    try:
        if os.path.exists(_APPROVAL_PATH):
            with open(_APPROVAL_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"pending": [], "approved": [], "note": "调优建议须人工在 .env 生效；本文件记录审批轨迹"}


def approve(recommendation_id: str, note: str = "") -> Dict[str, Any]:
    st = approval_state()
    st["approved"] = list(st.get("approved") or []) + [{"id": recommendation_id, "note": note, "ts": time.time()}]
    try:
        with open(_APPROVAL_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("[ThresholdTuner] 审批落盘失败: %s", e)
    return st
