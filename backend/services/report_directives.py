"""report_directives — 报告指挥层（2026-08-19 设计）。

日报/周报生成时，对分析结果产出一组「指挥指令」（directives）：
每条指令 = 触发条件 + 目标 + 动作。指挥层只做判定与触发，执行复用已有钩子。
指令随日报落库（payload.directives），前端可观测「日报指挥了什么」。

[轮63 2026-09-18] 分档从硬编码 ("scalp","midlong","long") 改为**按报告实际包含的车道**遍历：
报告层已收敛到「日内 / 长线趋势」双车道，硬编码旧枚举会让指令整段落空（sections 里根本没有
这些 key），且每条指令现在显式带 `lane`，落库时按车道分发到对应 payload。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from backend.config import lane_semantics as lane_sem

logger = logging.getLogger(__name__)


def _lane_sections(report: Dict[str, Any]) -> List[Any]:
    """把 report 里的段与车道配对，返回 [(lane, sec), ...]。

    优先用成对结构 `report["lanes"]` + `sections[lane]`（当前日报/周报的形态）。
    旧形态的 report（`sections` 的键就是车道名/旧 horizon）按节名解析，
    因此历史调用点与既有测试不需要跟着改。
    """
    sections = report.get("sections") or {}
    pairs: List[Any] = []
    lanes = report.get("lanes")
    if lanes:
        for raw in lanes:
            lane = lane_sem.get_spec(raw).lane
            sec = sections.get(lane)
            if sec is None:  # 键可能仍是旧写法
                sec = next((v for k, v in sections.items()
                            if lane_sem.lane_for_label(k) == lane), None)
            pairs.append((lane, sec or {}))
        if pairs:
            return pairs
    for key, sec in sections.items():
        pairs.append((lane_sem.get_spec(key).lane, sec or {}))
    return pairs


def analyze_directives(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """输入双车道日报 report，返回 directives 列表（每条含 lane）。"""
    directives: List[Dict[str, Any]] = []
    try:
        # D2 亏损币治理：以日报 symbol_daily（每币 pnl+笔数）驱动 symbol_penalty 状态机
        from backend.services.symbol_penalty import update_daily
        for lane, sec in _lane_sections(report):
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
                        "lane": lane,
                        "horizon": lane,  # 兼容旧读取端（字段名不变，值已规范化）
                        "action": "watchlist" if st.get("watchlisted") else "half_signal",
                        "pnl": round(pnl, 2),
                        "detail": st,
                    })
    except Exception as e:
        logger.debug("[ReportDirectives] D2 指令生成失败: %s", e)
    return directives
