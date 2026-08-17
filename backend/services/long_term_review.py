"""long_term_review — 长线趋势的周报深度复盘（非交易路径，LLM 可选）。

设计 V2 §4.4：LLM 只做非交易路径的深度复盘。本模块：
- 读 trend_cycles 结构化记忆，生成 R 分布/胜率/退出原因统计（纯规则，零 LLM）；
- LLM 生成定性总结（LLM_LONG_TERM_REVIEW=1 开启，默认关）——每周一次，绝不进交易热路径。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def build_weekly_report(db, account_id: int, days: int = 90) -> Dict[str, Any]:
    """汇总近 days 天的趋势周期记忆，生成周报数据（纯规则）。"""
    from sqlalchemy import func

    from backend.database.models import TrendCycle

    cutoff = None
    try:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    except Exception:
        pass

    q = db.query(TrendCycle)
    if account_id:
        q = q.filter(TrendCycle.account_id == int(account_id))
    if cutoff is not None:
        q = q.filter(TrendCycle.start_ts >= cutoff.replace(tzinfo=None))
    rows = q.order_by(TrendCycle.start_ts.desc()).all()

    rs = [r.total_r for r in rows if r.total_r is not None]
    reasons: Dict[str, int] = {}
    for r in rows:
        k = r.exit_reason or "unknown"
        reasons[k] = reasons.get(k, 0) + 1

    import statistics
    report = {
        "window_days": days,
        "cycles": len(rows),
        "mean_r": round(statistics.fmean(rs), 3) if rs else 0.0,
        "total_r": round(sum(rs), 2) if rs else 0.0,
        "win_rate": round(sum(1 for x in rs if x > 0) / len(rs), 3) if rs else 0.0,
        "exit_reasons": reasons,
        "by_symbol": {},
    }
    for r in rows:
        s = report["by_symbol"].setdefault(r.symbol, {"n": 0, "r": []})
        s["n"] += 1
        if r.total_r is not None:
            s["r"].append(r.total_r)
    for sym, s in report["by_symbol"].items():
        s["mean_r"] = round(statistics.fmean(s["r"]), 3) if s["r"] else 0.0
        del s["r"]
    return report


def llm_summary(report: Dict[str, Any], account_id: int = 0) -> Optional[str]:
    """LLM 生成周报定性总结（可选，默认关；失败静默跳过，不影响主流程）。"""
    try:
        import os
        if os.getenv("LLM_LONG_TERM_REVIEW", "0").strip().lower() not in ("1", "true", "yes", "on"):
            return None
        from backend.services.llm_config_service import get_llm_config_for_account, call_llm_api_sync

        cfg = get_llm_config_for_account(account_id) if account_id else None
        if not cfg:
            return None
        prompt = (
            f"你是长线趋势策略的复盘分析师。近 90 天趋势周期统计如下：\n"
            f"{report}\n\n"
            f"请用 3-5 句话总结：趋势质量如何、主要离场原因是否合理、"
            f"下一轮趋势应如何调整仓位/耐心。只输出结论，不输出代码。"
        )
        return call_llm_api_sync(cfg, [{"role": "user", "content": prompt}], caller="LongTermReview")
    except Exception as e:
        logger.debug("[LongTermReview] LLM 周报总结跳过: %s", e)
        return None
