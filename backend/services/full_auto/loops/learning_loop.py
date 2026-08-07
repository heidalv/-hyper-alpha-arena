"""
学习进化集成循环（整改#8 learning_loop 拆分）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Dict

if TYPE_CHECKING:
    from backend.services.full_auto_trading_service import FullAutoTradingService

logger = logging.getLogger(__name__)


def run_learning_integration(
    svc: "FullAutoTradingService",
    session_id: str,
    tick: int = 0,
    *,
    is_maintenance: bool = False,
) -> None:
    """全量接入学习进化组件到 full_auto 主循环。"""
    self = svc
    # [C1] 后台学习循环(由 coordinator 或维护循环驱动),设 system_identity 穿透 RLS,
    # 覆盖本函数内的 db / _db / _db4 等直接 SessionLocal。下方异步线程需另行设置
    # (线程不继承 ContextVar)。
    from backend.core.tenant import set_system_identity
    set_system_identity()
    from backend.config.settings import FULLAUTO_MAINTENANCE_EVERY_N_TICKS
    maint_every = max(1, FULLAUTO_MAINTENANCE_EVERY_N_TICKS if 'FULLAUTO_MAINTENANCE_EVERY_N_TICKS' in dir() else 5)

    # ── P0 层: 每 tick 轻量检测 ──
    try:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            # 概念漂移检测（30 tick 一次避免过度查询）
            if tick % 30 == 0:
                from backend.services.concept_drift_detector import concept_drift_detector
                drift = concept_drift_detector.detect("BTC", min_samples=20)
                if drift.get("drift_detected"):
                    logger.info(
                        f"[FullAuto] P0 概念漂移: BTC drift_score={drift.get('drift_score', 0):.4f}"
                    )
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[FullAuto] P0 集成异常（不影响主循环）: {e}")

    # ── P1 层: 叙事/反事实 ──
    try:
        # 交易叙事更新（每 30 tick，约 45 分钟）
        if tick % 30 == 0:
            from backend.database.connection import SessionLocal
            _db = SessionLocal()
            try:
                from backend.services.trading_narrative_engine import trading_narrative_engine
                trading_narrative_engine.build_narrative(_db)
            finally:
                _db.close()
    except Exception as e:
        logger.warning(f"[FullAuto] P1 叙事集成异常: {e}")

    # ── P2 层: 因子发现 + 跨周期挖掘 + Walk-Forward ──
    if is_maintenance or tick % maint_every == 0:
        try:
            # ML 全激活（#4/#10/#17/#18）— 异步离峰重训
            try:
                from backend.services.ml.activation_service import run_ml_activation_tick
                run_ml_activation_tick(session_id, tick, is_maintenance=is_maintenance)
            except Exception as _ml_exc:
                logger.warning(f"[FullAuto] ML 激活 tick 跳过: {_ml_exc}")

            # shadow→canary→full 晋升门（#6.2.6）— 离峰扫描 ML/因子/QAA 候选
            try:
                from backend.services.promotion_scan_service import run_promotion_scan_tick
                run_promotion_scan_tick(session_id, tick, is_maintenance=is_maintenance)
            except Exception as _pg_exc:
                logger.warning(f"[FullAuto] 晋升门扫描跳过: {_pg_exc}")

            import threading
            # 因子发现（异步，每天最多一次）
            def _run_discovery():
                try:
                    # [C1] 线程不继承 ContextVar,后台发现线程需自己设 system_identity。
                    from backend.core.tenant import set_system_identity as _set_sys_id
                    _set_sys_id()
                    from backend.database.connection import SessionLocal
                    _db2 = SessionLocal()
                    try:
                        from backend.services.factor_discovery import factor_discovery_engine
                        result = factor_discovery_engine.run_discovery(_db2)
                        if result.get("validated"):
                            logger.info(
                                f"[FullAuto] P2 因子发现: "
                                f"validated={len(result['validated'])}"
                            )
                    finally:
                        _db2.close()
                except Exception as exc:
                    logger.warning(f"[FullAuto] 因子发现线程异常: {exc}")

            threading.Thread(target=_run_discovery, daemon=True).start()

            # 跨周期挖掘（每个 symbol 每天最多一次）
            def _run_pattern_mining():
                try:
                    # [C1] 线程不继承 ContextVar,后台挖掘线程需自己设 system_identity。
                    from backend.core.tenant import set_system_identity as _set_sys_id
                    _set_sys_id()
                    from backend.database.connection import SessionLocal
                    _db3 = SessionLocal()
                    try:
                        from backend.services.opencode_bridge import (
                            run_cross_cycle_pattern_mining,
                        )
                        for sym in ["BTC", "ETH", "SOL"]:
                            run_cross_cycle_pattern_mining(_db3, symbol=sym)
                    finally:
                        _db3.close()
                except Exception as exc:
                    logger.warning(f"[FullAuto] 跨周期挖掘异常: {exc}")

            threading.Thread(target=_run_pattern_mining, daemon=True).start()

        except Exception as e:
            logger.warning(f"[FullAuto] P2 集成异常: {e}")

    # ── P3 层: A/B 实验 + 跨市场迁移 ──
    if is_maintenance or tick % (maint_every * 2) == 0:
        try:
            # A/B 实验超时检查
            from backend.services.learning_ab_framework import learning_ab_framework
            learning_ab_framework.check_timeout_experiments()

            # 跨市场迁移（每 2 个维护周期执行一次）
            if is_maintenance and tick % (maint_every * 4) == 0:
                try:
                    from backend.database.connection import SessionLocal
                    _db4 = SessionLocal()
                    try:
                        from backend.services.cross_market_transfer import cross_market_transfer
                        # BTC→ETH 知识迁移
                        cross_market_transfer.transfer_learned_patterns(
                            _db4, source_symbol="BTC", target_symbol="ETH"
                        )
                    finally:
                        _db4.close()
                except Exception as exc:
                    logger.warning(f"[FullAuto] 跨市场迁移异常: {exc}")

        except Exception as e:
            logger.warning(f"[FullAuto] P3 集成异常: {e}")

    # ── 整体学习状态快照（每60tick记录一次）──
    if tick % 60 == 0:
        try:
            from backend.services.walk_forward_validator import walk_forward_validator
            wf_status = walk_forward_validator.get_status()
            logger.info(
                f"[FullAuto] 学习健康: WF验证={wf_status.get('validations_total', 0)}, "
                f"通过率={wf_status.get('recent_pass_rate', 0):.0%}"
            )
        except Exception as e:
            logger.warning(f"[FullAuto] 学习健康快照异常: {e}")

_mlto_learning_last: Dict[str, float] = {}



def run_mlto_learning_tick(svc: "FullAutoTradingService", session_id: str) -> None:
    """PAPER_FAST_TRIAL：异步触发 paper 平仓学习兜底 + MLTO OWM 反哺。"""
    self = svc
    now = time.time()
    last = self._mlto_learning_last.get(session_id, 0)
    debounce = 45
    try:
        from backend.services.paper_pace_controller import paper_pace_controller
        debounce = max(30, paper_pace_controller.get_tick_seconds())
    except Exception as _pace_exc:
        logger.warning(f"[FullAuto] paper_pace_controller 不可用，debounce 回退 45s: {_pace_exc}")
    if now - last < debounce:
        return
    self._mlto_learning_last[session_id] = now

    def _worker():
        try:
            from backend.services.learning_loop_service import learning_loop
            r1 = learning_loop.trigger_job("paper_outcome_backfill")
            r2 = learning_loop.trigger_job("outcome_batch")
            if r1.get("ok") or r2.get("ok"):
                logger.info(
                    "[FullAuto] MLTO 学习 tick session=%s backfill=%s batch=%s",
                    session_id[:12],
                    r1.get("ok"),
                    r2.get("ok"),
                )
        except Exception as exc:
            logger.warning("[FullAuto] MLTO 学习 tick 异常: %s", exc)
        # MidLong v2 Phase4：概念信念复盘（节流在模块内）
        try:
            from backend.database.connection import SessionLocal
            from backend.services.mlto.midlong_belief_loop import maybe_run_belief_review
            _db = SessionLocal()
            try:
                maybe_run_belief_review(session_id=session_id, db=_db)
            finally:
                try:
                    _db.close()
                except Exception as _close_exc:
                    logger.warning(f"[FullAuto] belief 会话关闭异常: {_close_exc}")
        except Exception as _bel_exc:
            logger.warning("[FullAuto] MidLong belief review skip: %s", _bel_exc)

    threading.Thread(
        target=_worker,
        daemon=True,
        name=f"mlto-learn-{session_id[:8]}",
    ).start()

