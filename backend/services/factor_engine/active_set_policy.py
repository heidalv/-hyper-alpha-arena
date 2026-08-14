"""因子活跃集加载契约（SSOT）。

双池存储保持隔离（AST 进化仓 vs 公式商店）；「谁算活跃」必须经本模块 role→states，
禁止各处散落 FactorActiveSet.state.in_([...])。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Iterable, List, Optional


class ActiveSetRole(str, Enum):
    TRADABLE = "tradable"  # Engine / Router / Exposure 热路径
    RESEARCH = "research"  # 进化池、在线权重、DSR 分母
    SHADOW = "shadow"  # 影子推进复评
    GOVERNANCE_ACTIVE = "gov_active"  # ACTIVE 容量帽
    UI_TOP = "ui_top"  # 看板可交易视图（= TRADABLE）
    QUARANTINE = "quarantine"  # 运维台：隔离区（不进交易热路径）


# TRADABLE 冻结为现热路径，禁止把 ORTHO 塞进交易面。
# [2026-08-14 P1-C4 拍板定稿（用户确认）]：PAPER 保持可交易（现状）——影子期因子
# 允许进入 Engine/Router/Exposure 热路径，这是刻意设计（可交易=PAPER/SMALL_LIVE/ACTIVE，
# 与 Router 热路径一致，ops 面板 callout 同步）。与 lifecycle 文档 "PAPER=纸上影子"
# 的语义差异由两条机制补偿：① PAPER 因子在线权重受 PAPER_FACTOR_WEIGHT_CAP 上限
# （factor_evaluation_pipeline 强制）；② 晋升 SMALL_LIVE/ACTIVE 仍需 OversightAgent
# 审批（lifecycle.APPROVAL_REQUIRED）。
STATES: dict[ActiveSetRole, frozenset[str]] = {
    ActiveSetRole.TRADABLE: frozenset({"PAPER", "SMALL_LIVE", "ACTIVE"}),
    ActiveSetRole.RESEARCH: frozenset({"ORTHO", "PAPER", "SMALL_LIVE", "ACTIVE"}),
    ActiveSetRole.SHADOW: frozenset({"PAPER", "SMALL_LIVE"}),
    ActiveSetRole.GOVERNANCE_ACTIVE: frozenset({"ACTIVE"}),
    ActiveSetRole.UI_TOP: frozenset({"PAPER", "SMALL_LIVE", "ACTIVE"}),
    ActiveSetRole.QUARANTINE: frozenset({"QUARANTINE"}),
}


def states_for(role: ActiveSetRole) -> frozenset[str]:
    return STATES[role]


def load_factor_active_rows(
    role: ActiveSetRole,
    *,
    parse_expr: bool = False,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """唯一 DB 读入口：按 role 过滤 factor_active_set。"""
    from backend.database.connection import AnalyticsSessionLocal
    from backend.database.models import FactorActiveSet

    wanted = states_for(role)
    out: List[Dict[str, Any]] = []
    db = AnalyticsSessionLocal()
    try:
        q = db.query(FactorActiveSet).filter(FactorActiveSet.state.in_(list(wanted)))
        rows = q.all()
        if role in (ActiveSetRole.TRADABLE, ActiveSetRole.UI_TOP):
            rows = sorted(
                rows,
                key=lambda r: (
                    0 if str(r.factor_id or "").startswith("s5m_") else 1,
                    0 if str(r.state) == "ACTIVE" else 1,
                    -abs(float(r.icir or 0)),
                ),
            )
        if limit is not None:
            rows = rows[: max(0, int(limit))]

        parser = None
        if parse_expr:
            from backend.services.factor_engine.expr.parser import parse as _parse
            parser = _parse

        for r in rows:
            item: Dict[str, Any] = {
                "factor_id": r.factor_id,
                "expr_ast": r.expr_ast,
                "expr_id": r.expr_id,
                "source": r.source,
                "state": r.state,
                "icir": r.icir,
                "incremental_corr": r.incremental_corr,
                "capacity_usd": r.capacity_usd,
                "last_net_ic": getattr(r, "last_net_ic", None),
                "turnover": getattr(r, "turnover", None),
                "evaluated_cycles": getattr(r, "evaluated_cycles", None),
                "current_weight": r.current_weight or {},
                "activated_at": r.activated_at,
                "deactivated_at": getattr(r, "deactivated_at", None),
                "last_evaluated_at": getattr(r, "last_evaluated_at", None),
            }
            if parser is not None and r.expr_ast:
                try:
                    item["expr"] = parser(r.expr_ast)
                except Exception:
                    continue
            elif parse_expr:
                continue
            out.append(item)
    finally:
        db.close()
    return out


def is_tradable_state(state: str | None) -> bool:
    return str(state or "") in STATES[ActiveSetRole.TRADABLE]
