"""
维护 housekeeping 循环（整改#8 maintenance_loop 拆分）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.full_auto_trading_service import FullAutoTradingService

logger = logging.getLogger(__name__)


def run_maintenance_cycle(svc: "FullAutoTradingService", session_id: str) -> None:
    """辅助 housekeeping：策略创建/淘汰、V3、模板、风控。"""
    self = svc
    # [C1] 后台维护循环(可由 coordinator 或独立调度器驱动),设 system_identity 穿透 RLS,
    # 覆盖本函数内 _db 以及 _run_health_check / _run_learning_integration 各自开的 SessionLocal。
    from backend.core.tenant import set_system_identity
    set_system_identity()
    self._run_health_check(session_id, maintenance_only=True)
    # P3.4: 维护周期中运行学习集成（全量模式）
    tick = self._unified_tick_count.get(session_id, 0)
    self._run_learning_integration(session_id, tick, is_maintenance=True)

    # #9 Phase4：写路径 DB 退役 — 投影→DB 镜像同步
    try:
        from backend.database.connection import SessionLocal
        from backend.services.event_sourcing.phase4 import run_retirement_sync

        _db = SessionLocal()
        try:
            run_retirement_sync(_db)
        finally:
            _db.close()
    except Exception as _es4_err:
        logger.debug("[FullAuto] Phase4 DB 镜像同步跳过: %s", _es4_err)

    # S2-8：AI 决策置信度校准样本回填（paper 平仓盈亏 → ai_decision_logs）
    # 幂等，低频跑；样本就绪后 ai_decision_calibrator 自动拟合 conf→胜率曲线。
    try:
        from backend.services.calibration.decision_pnl_backfill import (
            run_backfill_once,
        )
        run_backfill_once(lookback_days=90)
    except Exception as _bf_err:
        logger.debug("[FullAuto] PnlBackfill 跳过: %s", _bf_err)

    # S2-10c：QAA 域调度统一（域注册表 + 统一心跳 + 统一调度）
    # 总开关与域级开关默认关闭，开启后按各域间隔在维护周期中调度 tick。
    try:
        from backend.services.qaa_scheduler import (
            run_due_domains,
            get_scheduler_status,
        )
        ran = run_due_domains(svc=svc, session_id=session_id)
        if ran:
            logger.info("[FullAuto] QAA 统一调度执行: %s", ran)
        _status = get_scheduler_status()
        if _status.get("enabled"):
            logger.debug(
                "[FullAuto] QAA 调度心跳: %s",
                {k: v.get("last_status") for k, v in _status.get("domains", {}).items()},
            )
    except Exception as _qaa_err:
        logger.debug("[FullAuto] QAA 统一调度跳过: %s", _qaa_err)

