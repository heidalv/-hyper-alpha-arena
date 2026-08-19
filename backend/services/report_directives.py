"""report_directives — 报告指挥层（2026-08-19 设计）。

日报/周报生成时，对分析结果产出一组「指挥指令」（directives）：
每条指令 = 触发条件 + 目标 + 动作。指挥层只做判定与触发，执行复用已有钩子。
指令随日报落库（payload.directives），前端可观测「日报指挥了什么」。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def analyze_directives(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """输入三周期日报 report，返回 directives 列表。"""
    directives: List[Dict[str, Any]] = []
    try:
        # D2 亏损币治理：以日报 symbol_daily（每币 pnl+笔数）驱动 symbol_penalty 状态机
        from backend.services.symbol_penalty import update_daily
        for horizon in ("scalp", "midlong", "long"):
            sec = (report.get("sections") or {}).get(horizon) or {}
            daily = sec.get("symbol_daily") or []
            if not daily:
                la = sec.get("loss_attribution") or {}
                daily = [
                    {"symbol": str(it.get("key") or ""), "pnl": float(it.get("pnl") or 0.0),
                     "n": int(it.get("n") or 0)}
                    for it in (la.get("by_symbol_all") or la.get("by_symbol") or [])
                ]
            for item in daily:
                sym = str(item.get("symbol") or item.get("key") or "")
                pnl = float(item.get("pnl") or 0.0)
                n = int(item.get("n") or 0)
                if not sym or n < 1:
                    continue
                st = update_daily(sym, pnl, n, report.get("report_date") or "")
                if st.get("watchlisted") or float(st.get("penalty", 1.0)) < 1.0:
                    directives.append({
                        "type": "symbol_penalty",
                        "symbol": sym,
                        "horizon": horizon,
                        "action": "watchlist" if st.get("watchlisted") else "half_signal",
                        "pnl": round(pnl, 2),
                        "detail": st,
                    })
    except Exception as e:
        logger.debug("[ReportDirectives] D2 指令生成失败: %s", e)
    try:
        # D1 超时治理：短线段超时退出占比 > 35% → 触发 TP/SL 网格重训指令
        sec = (report.get("sections") or {}).get("scalp") or {}
        _exits = sec.get("exit_stats") or {}
        total = int(_exits.get("total_exits") or 0)
        timeout = int(_exits.get("max_hold_timeout") or 0)
        if total > 0 and (timeout / total) > 0.35:
            directives.append({
                "type": "tp_sl_retrain",
                "horizon": "scalp",
                "action": "retrain",
                "timeout_ratio": round(timeout / total, 3),
                "detail": "max_hold_timeout 占比超 35%，触发 TP/SL 网格重训（近 30 天窗口）",
            })
    except Exception as e:
        logger.debug("[ReportDirectives] D1 指令生成失败: %s", e)
    return directives
