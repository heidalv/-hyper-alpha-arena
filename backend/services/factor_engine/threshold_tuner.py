"""自动阈值调优（升级计划 v3.0 S3/R5 · 对标 Freqtrade hyperopt / Two Sigma SigOpt）。

框架：阈值向量 → 评价函数（回放/回测）→ 网格/贝叶斯搜索 → 报告落盘 →
shadow 灰度（人工审批后才可 env 生效）。**调优结果绝不自动改阈值**。

V1 域：
- "long_rule"：L1 阈值 × 前瞻网格（复用 long_rule_validator 同口径诊断，
  评价 = OOS Sharpe，附 DSR/PBO）——已可用；
- "scalp_router"：[2026-08-29 P2.5 已实现] 基于 scalp_signal_log 已结算信号
  回放（score 桶 × 胜率/费后净收益），推荐执行门槛的最低正EV档。

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
    """scalp 路由执行门槛回放调优（[2026-08-29 P2.5] 补齐——数据源已就绪）。

    数据源 = scalp_signal_log 已结算行（factor_score / threshold / win / net_ret
    均已持久化；旧 TODO 注释"分数未落库"已过时）。评价：按 5 分桶统计
    胜率与费后净收益，推荐 = 最低的"该桶及以上全部 net>0"的分数档，
    对照当前 SCALP_FACTOR_EXECUTE_THRESHOLD。shadow-only，绝不自动改阈值。
    """
    from sqlalchemy import text as _sa_text

    lookback_days = _env_int("SCALP_TUNE_LOOKBACK_DAYS", 30)
    min_n_bucket = max(30, _env_int("SCALP_TUNE_MIN_BUCKET_N", 200))
    with _arena_session() as db:
        rows = db.execute(_sa_text(
            "SELECT factor_score, win, net_ret FROM scalp_signal_log "
            "WHERE settled = true AND signal_ts > extract(epoch from now()) - :days*86400"
        ), {"days": lookback_days}).fetchall()
    if not rows:
        return {"domain": "scalp_router", "status": "no_data",
                "note": f"近{lookback_days}天无已结算信号"}

    buckets: Dict[int, Dict[str, float]] = {}
    for score, win, net in rows:
        b = int((float(score or 0)) // 5) * 5
        st = buckets.setdefault(b, {"n": 0, "wins": 0, "net": 0.0})
        st["n"] += 1
        st["wins"] += int(bool(win))
        st["net"] += float(net or 0)
    table = []
    for b in sorted(buckets):
        st = buckets[b]
        if st["n"] < min_n_bucket:
            continue
        table.append({
            "score_min": b, "n": st["n"],
            "win_rate": round(st["wins"] / st["n"], 4),
            "avg_net_ret": round(st["net"] / st["n"], 6),
        })

    # 推荐 = 最低的"该档及以上全部桶 avg_net_ret>0"的档位（向上单调正EV带）
    ordered = sorted(table, key=lambda r: r["score_min"])
    rec_score = None
    for i, cand in enumerate(ordered):
        if all(r["avg_net_ret"] > 0 for r in ordered[i:]):
            rec_score = cand["score_min"]
            break
    current = _env_int("SCALP_FACTOR_EXECUTE_THRESHOLD", 35)
    return {
        "domain": "scalp_router",
        "status": "ok",
        "lookback_days": lookback_days,
        "n_signals": len(rows),
        "buckets": table,
        "current_execute_threshold": current,
        "recommended_execute_threshold": rec_score,
        "recommendation": (
            f"SCALP_FACTOR_EXECUTE_THRESHOLD={rec_score}（该档及以上全部桶费后净收益>0）"
            if rec_score is not None else
            "无正EV分数带——任何门槛都无法盈利，应先修信号/出场结构再调门槛"
        ),
        "note": "shadow-only：调优建议须人工审批后写入 .env 方可生效",
    }


def _arena_session():
    """arena 库会话上下文（RLS 场景由应用会话注入租户上下文）。"""
    from contextlib import contextmanager
    from backend.database.connection import SessionLocal

    @contextmanager
    def _cm():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    return _cm()


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
