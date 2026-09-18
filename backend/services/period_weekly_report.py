"""period_weekly_report — 双车道统一周报（轮63 重构，2026-09-18）。

每周汇总**两条交易车道**（日内 / 长线趋势）：交易统计（近 7 天）+ 周亏损归因
+ 长线趋势车道的 TrendCycle R 分布（复用 `long_term_review.build_weekly_report`）
+ 每条车道各自的 LLM 定性总结。

## 轮63 改了什么

- 分档从硬编码 `("scalp","midlong","long")` 改为 `lane_semantics.report_lanes()`：
  短线车道已停，旧版每周都会多出一段几乎全空的 `scalp`，而前端 `HORIZON_LABEL`
  里没有它的中文名 → 渲染成 `undefined 周报`（实机可见）。
- 每段带 `lane_identity`（tier/nature/主看周期/期望持仓/实测持仓），前端不再靠外层 tab 猜。
- LLM 总结**按车道分开**（`llm_summary` 落在各段内），不再给一句三周期混合的总结。

中线专项指标（同向再开率/分档 TP 触达率）由既有 `midlong_weekly_report` 产出，本模块不重复实现。
纯规则 + 非交易路径。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from backend.config import lane_semantics as lane_sem

logger = logging.getLogger(__name__)

# 进程内缓存：最新周报（API 读取，避免每次请求重算）
_LATEST_WEEKLY: Dict[str, Any] = {}


def build_weekly_report(db, account_id: int, days: int = 7) -> Dict[str, Any]:
    """生成双车道周报 dict。"""
    from backend.services.loss_attribution import build_loss_attribution
    from backend.services.period_daily_report import _open_positions, _trades_since

    sections: Dict[str, Any] = {}
    for lane in lane_sem.report_lanes():
        spec = lane_sem.get_spec(lane)
        sec: Dict[str, Any] = {
            "lane_identity": spec.identity(),
            "trades_7d": _trades_since(db, account_id, lane, hours=days * 24),
            "open_positions": _open_positions(db, account_id, lane),
            "loss_attribution": build_loss_attribution(db, account_id, lane, days=days),
        }
        if lane == lane_sem.LANE_TREND:
            try:
                from backend.services.long_term_review import build_weekly_report as _lw
                sec["trend_cycles"] = _lw(db, account_id, days=days)
            except Exception as e:
                logger.debug("[PeriodWeekly] 长线周期统计失败: %s", e)
                sec["trend_cycles"] = {"error": str(e)[:100]}
        sections[lane] = sec

    report: Dict[str, Any] = {
        "account_id": int(account_id),
        "window_days": days,
        "lanes": list(lane_sem.report_lanes()),
        "sections": sections,
    }
    # LLM 定性总结（可选，按车道分开写进各段）
    if _llm_enabled():
        for lane, sec in sections.items():
            try:
                summary = _llm_weekly(db, account_id, lane, sec, days)
                if summary:
                    sec["llm_summary"] = summary
            except Exception as e:
                logger.debug("[PeriodWeekly] LLM 总结失败(%s): %s", lane, e)
    _LATEST_WEEKLY[str(account_id)] = report
    return report


def _llm_enabled() -> bool:
    return os.getenv("LLM_LONG_TERM_REVIEW", "0").strip().lower() in ("1", "true", "yes", "on")


def _llm_weekly(db, account_id: int, lane: str, sec: Dict[str, Any], days: int) -> Optional[str]:
    import json as _json

    from backend.services.llm_config_service import call_llm_api_sync, get_llm_config_for_account

    cfg = get_llm_config_for_account(account_id) if account_id else None
    if not cfg:
        return None
    spec = lane_sem.get_spec(lane)
    brief = _json.dumps(sec, ensure_ascii=False, default=str)[:4000]
    prompt = (
        f"你是加密货币交易系统的「{spec.label_full}」车道周报分析师。\n"
        f"该车道周期身份：主看 {spec.report_timeframe} K线"
        f"（确认周期 {'/'.join(spec.confirm_timeframes)}），"
        f"设计期望持仓 {spec.expected_hold_hours:g}h，期望区间 {lane_sem.hold_bracket_label(lane)}。\n"
        f"以下是近 {days} 天该车道周报数据：\n{brief}\n\n"
        f"请用 4-6 句话只总结**这一条车道**：本周表现与趋势质量、亏损根因、"
        f"持仓时长是否偏离该车道应有的周期、下周仓位与耐心建议。不要提其它车道。只输出结论。"
    )
    return call_llm_api_sync(cfg, [{"role": "user", "content": prompt}], caller="PeriodWeeklyReport")


def get_latest_weekly(account_id: int) -> Optional[Dict[str, Any]]:
    return _LATEST_WEEKLY.get(str(account_id))


def run_weekly_reports() -> Dict[str, Any]:
    """周 cron 入口：遍历活跃会话生成周报（内存缓存，API 读取）。"""
    out = {"sessions": 0, "built": 0, "error": None, "lanes": list(lane_sem.report_lanes())}
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
