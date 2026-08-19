"""period_weekly_report — 三周期统一周报（2026-08-19 报告观测体系）。

每周汇总三周期：交易统计（近 7 天）+ 周亏损归因 + 长线趋势周期 R 分布
（TrendCycle 归档，复用 long_term_review.build_weekly_report）+ LLM 定性总结。
中线专项指标（同向再开率/分档 TP 触达率）由既有 midlong_weekly_report 产出，本模块不重复实现。
纯规则 + 非交易路径。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 进程内缓存：最新周报（API 读取，避免每次请求重算）
_LATEST_WEEKLY: Dict[str, Any] = {}


def build_weekly_report(db, account_id: int, days: int = 7) -> Dict[str, Any]:
    """生成三周期周报 dict。"""
    from backend.services.loss_attribution import build_loss_attribution
    from backend.services.period_daily_report import _trades_since, _open_positions

    sections = {}
    for horizon in ("scalp", "midlong", "long"):
        sec = {
            "trades_7d": _trades_since(db, account_id, horizon, hours=days * 24),
            "open_positions": _open_positions(db, account_id, horizon),
            "loss_attribution": build_loss_attribution(db, account_id, horizon, days=days),
        }
        if horizon == "long":
            try:
                from backend.services.long_term_review import build_weekly_report as _lw
                sec["trend_cycles"] = _lw(db, account_id, days=days)
            except Exception as e:
                logger.debug("[PeriodWeekly] 长线周期统计失败: %s", e)
                sec["trend_cycles"] = {"error": str(e)[:100]}
        sections[horizon] = sec

    report = {"account_id": int(account_id), "window_days": days, "sections": sections}
    # LLM 定性总结（可选）
    try:
        import os
        if os.getenv("LLM_LONG_TERM_REVIEW", "0").strip().lower() in ("1", "true", "yes", "on"):
            report["llm_summary"] = _llm_weekly(db, account_id, report)
    except Exception:
        pass
    _LATEST_WEEKLY[str(account_id)] = report
    return report


def _llm_weekly(db, account_id: int, report: Dict[str, Any]) -> Optional[str]:
    import json as _json
    from backend.services.llm_config_service import get_llm_config_for_account, call_llm_api_sync
    cfg = get_llm_config_for_account(account_id) if account_id else None
    if not cfg:
        return None
    brief = _json.dumps(report, ensure_ascii=False, default=str)[:4000]
    prompt = (
        f"你是加密货币三周期交易系统的周报分析师。以下是近 7 天三周期周报数据：\n{brief}\n\n"
        f"请用 4-6 句话总结：各周期本周表现与趋势质量、亏损周期根因、下周仓位与耐心建议。只输出结论。"
    )
    return call_llm_api_sync(cfg, [{"role": "user", "content": prompt}], caller="PeriodWeeklyReport")


def get_latest_weekly(account_id: int) -> Optional[Dict[str, Any]]:
    return _LATEST_WEEKLY.get(str(account_id))


def run_weekly_reports() -> Dict[str, Any]:
    """周 cron 入口：遍历活跃会话生成周报（内存缓存，API 读取）。"""
    out = {"sessions": 0, "built": 0, "error": None}
    try:
        # [2026-08-19] cron 后台线程无 HTTP 上下文，设管理员级身份穿透 RLS（同日报）。
        from backend.core.tenant import set_system_identity
        set_system_identity()
        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession
        db = SessionLocal()
        try:
            sessions = db.query(FullAutoSession).filter(
                FullAutoSession.status.in_(["running", "defensive"])
            ).all()
            out["sessions"] = len(sessions)
            for s in sessions:
                acct = getattr(s, "paper_account_id", None) or getattr(s, "account_id", None)
                if not acct:
                    continue
                try:
                    build_weekly_report(db, int(acct))
                    out["built"] += 1
                except Exception as e:
                    logger.warning("[PeriodWeekly] 会话 %s 周报失败: %s", s.session_id, e)
        finally:
            db.close()
    except Exception as e:
        out["error"] = str(e)[:200]
        logger.warning("[PeriodWeekly] 周报任务失败: %s", e)
    return out
