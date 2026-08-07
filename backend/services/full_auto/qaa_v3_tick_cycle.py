"""QAA v3 tick — 从 monolith _run_qaa_v3_tick 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Set

logger = logging.getLogger(__name__)


@dataclass
class QaaV3TickHost:
    active_db_sessions: Dict[str, Any]
    market_scan_cache: Dict[str, Any]
    market_scan_cache_ts: float
    active_positions_cache: list
    unified_tick_count: Dict[str, int]
    pre_screen_results: Any = None
    pre_screen_passed: Set[str] = field(default_factory=set)
    qaa_ctx: Any = None
    qaa_last_decision: Any = None
    orch_bg_thread: Any = None

    bootstrap_qaa_v3_context: Callable = field(repr=False, default=lambda *a, **k: False)
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    bootstrap_market_summary: Callable = field(repr=False, default=lambda *a, **k: {})
    get_or_capture_unified_snapshot: Callable = field(repr=False, default=lambda *a, **k: None)
    sanitize_market_summary_for_qaa: Callable = field(repr=False, default=lambda m: m)
    run_analyst_system_v3: Callable = field(repr=False, default=lambda *a, **k: None)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)


def build_qaa_v3_tick_host(svc) -> QaaV3TickHost:
    host = QaaV3TickHost(
        active_db_sessions=svc._active_db_sessions,
        market_scan_cache=svc._market_scan_cache,
        market_scan_cache_ts=svc._market_scan_cache_ts,
        active_positions_cache=svc._active_positions_cache,
        unified_tick_count=svc._unified_tick_count,
        pre_screen_results=getattr(svc, "_pre_screen_results", None),
        pre_screen_passed=set(getattr(svc, "_pre_screen_passed", None) or []),
        qaa_ctx=getattr(svc, "_qaa_ctx", None),
        qaa_last_decision=getattr(svc, "_qaa_last_decision", None),
        orch_bg_thread=getattr(svc, "_orch_bg_thread", None),
        get_trading_account_id=svc._get_trading_account_id,
        bootstrap_market_summary=svc._bootstrap_market_summary,
        get_or_capture_unified_snapshot=svc._get_or_capture_unified_snapshot,
        sanitize_market_summary_for_qaa=svc._sanitize_market_summary_for_qaa,
        run_analyst_system_v3=svc._run_analyst_system_v3,
        safe_commit=svc._safe_commit,
    )

    def _bootstrap(blocking: bool = False) -> bool:
        ok = svc.bootstrap_qaa_v3_context(blocking=blocking)
        host.qaa_ctx = getattr(svc, "_qaa_ctx", None)
        host.qaa_last_decision = getattr(svc, "_qaa_last_decision", None)
        return ok

    host.bootstrap_qaa_v3_context = _bootstrap
    return host


def run_qaa_v3_tick(session_id: str, host: QaaV3TickHost) -> None:
    from backend.database.connection import SessionLocal
    from backend.database.models import FullAutoSession

    _t0 = time.time()

    # 获取 QAAContext（未就绪则跳过，避免阻塞 tick 线程）
    if host.qaa_ctx is None:
        host.bootstrap_qaa_v3_context(blocking=False)
    if host.qaa_ctx is None:
        logger.warning("[FullAuto][QAA v3] QAAContext 未初始化，跳过本 tick 等待启动完成")
        return
    qaa_ctx = host.qaa_ctx

    # ══════════════════════════════════════════════════════
    # Phase A: 短生命周期 session 读取数据
    # ══════════════════════════════════════════════════════
    session_status = None
    session_orm_id = None
    active_ids = []
    symbols = []
    account_id = None
    market_summary = {}
    account_equity = 0.0
    daily_pnl = 0.0

    _db_a = SessionLocal()
    _db_track_key = f"{session_id}:qaa_v3_tick"
    host.active_db_sessions[_db_track_key] = _db_a
    try:
        session_row = _db_a.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session_row or session_row.status not in ("running", "defensive"):
            return
        session_status = session_row.status
        session_orm_id = session_row.id
        active_ids = list(session_row.active_strategy_ids or [])
        if not active_ids:
            return
        symbols = list(session_row.symbols or [])
        account_equity = float(getattr(session_row, "account_equity", 0) or 0)
        daily_pnl = float(getattr(session_row, "daily_pnl", 0) or 0)
        account_id = host.get_trading_account_id(_db_a, session_row)

        market_summary = host.bootstrap_market_summary(symbols)
        for _sym, _info in (market_summary or {}).items():
            if isinstance(_info, dict) and _info.get("current_price"):
                host.market_scan_cache.setdefault(_sym, {}).update(_info)
        if market_summary:
            host.market_scan_cache_ts = time.time()

        # 从统一数据池获取实时快照
        try:
            from backend.services.unified_data_pool import unified_data_pool
            snap = host.get_or_capture_unified_snapshot(
                symbols=symbols,
                account_id=account_id,
                include_klines=False,
                light_mode=True,
                max_age=120.0,
            )
            if snap:
                unified_data_pool.merge_snapshot_into_market_summary(
                    market_summary, snap, symbols,
                )
        except Exception as _snap_err:
            logger.warning(f"[FullAuto][QAA v3] 快照合并失败(非致命): {_snap_err}")

        # 加载活跃持仓
        try:
            from backend.services.paper_trading_engine import paper_engine
            positions = paper_engine.get_positions(_db_a, account_id) or []
            host.active_positions_cache = positions
        except Exception as _pos_err:
            logger.debug(f"[FullAuto][QAA v3] 持仓加载失败: {_pos_err}")
    except Exception as e:
        logger.error(f"[FullAuto][QAA v3] Phase A 数据读取异常: {e}", exc_info=True)
        return
    finally:
        host.active_db_sessions.pop(_db_track_key, None)
        _db_a.close()

    # ── 编排器数据：从独立后台线程的缓存读取 ──
    _orch_filled = 0
    for sym in symbols:
        if sym in market_summary and isinstance(market_summary[sym], dict):
            _ms_orch = market_summary[sym].get("orchestrator")
            if not _ms_orch or not isinstance(_ms_orch, dict) or not _ms_orch.get("long_bias"):
                _cache_orch = (host.market_scan_cache.get(sym) or {}).get("orchestrator")
                if _cache_orch and isinstance(_cache_orch, dict) and _cache_orch.get("long_bias"):
                    market_summary[sym]["orchestrator"] = _cache_orch
                    market_summary[sym]["recommended_nature"] = host.market_scan_cache.get(sym, {}).get("recommended_nature")
                    _orch_filled += 1
    if _orch_filled:
        logger.info(
            f"[FullAuto][QAA v3] 编排器缓存回填: {_orch_filled}/{len(symbols)} 个币种"
        )
    elif host.orch_bg_thread and host.orch_bg_thread.is_alive():
        logger.debug(
            "[FullAuto][QAA v3] 编排器后台线程运行中，暂无缓存数据"
        )

    # ══════════════════════════════════════════════════════
    # Phase B: 内存计算 + AI 分析（不持有任何 DB session）
    # ══════════════════════════════════════════════════════

    # ── Phase 3.1: 写入 DeterministicState ──
    state_manager = None
    try:
        state_manager = qaa_ctx.state_manager
        if state_manager is not None:
            det_state = state_manager.get_deterministic_state("trading")
            if det_state is not None:
                positions_data = []
                for pos in host.active_positions_cache:
                    if isinstance(pos, dict):
                        positions_data.append(pos)
                det_state.update_items({
                    s: host.market_scan_cache.get(s, {})
                    for s in symbols
                })
                det_state.update_balance(
                    balance=account_equity,
                    total=account_equity,
                    daily_usage=abs(daily_pnl),
                )
    except Exception as _state_err:
        logger.debug(f"[FullAuto][QAA v3] DeterministicState 写入失败: {_state_err}")

    # ── 混合信号模式：预筛选注入（QAA v3 路径）──
    _prescreen_force_analyze = False
    try:
        from backend.config.settings import HYBRID_SIGNAL_MODE_ENABLED, PRESCREENER_ENABLED
        if HYBRID_SIGNAL_MODE_ENABLED and PRESCREENER_ENABLED:
            from backend.services.signal_frequency_guard import get_signal_frequency_guard
            from backend.services.signal_pre_screener import get_signal_pre_screener
            _screener = get_signal_pre_screener()
            _freq_guard = get_signal_frequency_guard()
            _batch = _screener.screen_batch(symbols, market_summary or {}, tier="short")
            _guaranteed = _freq_guard.get_guaranteed_symbols("short", symbols, market_summary or {})
            _ps_passed = set(_batch.passed_symbols + _guaranteed)
            host.pre_screen_results = _batch
            host.pre_screen_passed = _ps_passed
            if _ps_passed:
                _prescreen_force_analyze = True
                logger.info(
                    f"[FullAuto][QAA v3][混合模式] 预筛选通过 {len(_ps_passed)}/{len(symbols)} "
                    f"+ 频率保障 {len(_guaranteed)} → 强制LLM分析"
                )
            else:
                logger.debug(
                    f"[FullAuto][QAA v3][混合模式] 预筛选通过 0/{len(symbols)}"
                )
    except Exception as _ps_err:
        logger.debug(f"[FullAuto][QAA v3][混合模式] 预筛选跳过(非致命): {_ps_err}")

    # ── TickOrchestrator.run_tick ──
    decision = {}
    try:
        tick_orchestrator = qaa_ctx.tick_orchestrator
        _highest_anomaly = 0.0
        _max_orch_confidence = 0.0
        try:
            _ar = anomaly_reports  # noqa: F821 (defined in legacy path)
            if _ar:
                _highest_anomaly = max(
                    (getattr(r, "total_anomaly_score", 0) or 0)
                    for r in _ar.values()
                )
        except NameError:
            pass
        try:
            _od = orchestrator_decisions  # noqa: F821 (defined in legacy path)
            if _od:
                _max_orch_confidence = max(
                    (d.get("confidence", 0) or 0)
                    for d in _od.values()
                    if isinstance(d, dict)
                )
        except NameError:
            pass
        _score = max(_highest_anomaly, _max_orch_confidence / 100.0)
        _severity = (
            "HIGH" if _score >= 0.5
            else "EXTREME" if _score >= 0.7
            else "NORMAL"
        )
        input_data = {
            "session_id": session_id,
            "symbols": symbols,
            "market_summary": host.sanitize_market_summary_for_qaa(market_summary),
            "active_ids": active_ids,
            "has_active_items": bool(host.active_positions_cache),
            "open_position_count": len(host.active_positions_cache),
            "current_score": _score,
            "severity_level": _severity,
        }
        if _score > 0:
            logger.info(
                f"[FullAuto][QAA v3] 注入分数: score={_score:.3f}, "
                f"severity={_severity} (anomaly={_highest_anomaly:.2f}, "
                f"orch={_max_orch_confidence:.0f})"
            )
        run = tick_orchestrator.run_tick(
            domain="trading",
            input_data=input_data,
        )
        decision = run.decision if run and run.decision else {}
        sc_available = hasattr(self, '_qaa_last_decision') and host.qaa_last_decision is not None
        sc_decision = host.qaa_last_decision if sc_available else None
        decision_action = decision.get("action", "")
        decision_confidence = decision.get("confidence", 0)
        logger.info(
            f"[FullAuto][QAA v3] 决策诊断: "
            f"run_devision_exists={bool(run and run.decision)}, "
            f"decision_keys={list(decision.keys())}, "
            f"action={decision_action}, confidence={decision_confidence}, "
            f"has_sc={sc_available}, "
            f"sc_action={sc_decision.get('action') if sc_decision else None}, "
            f"id_self={id(self)}"
        )
        if sc_available:
            sc_action = sc_decision.get("action", "")
            sc_confidence = sc_decision.get("confidence", 0)
            use_sc = (
                not decision_action
                or decision_action not in ("hold", "execute", "cancel", "buy", "sell")
                or decision_confidence < sc_confidence
                or decision_action == "hold" and sc_action in ("execute", "buy", "sell", "cancel")
            )
            if use_sc:
                decision = sc_decision
                logger.info(
                    f"[FullAuto][QAA v3] 侧通道读取 handler 决策: "
                    f"action={decision.get('action')}, confidence={decision.get('confidence')}"
                )
    except Exception as _tick_err:
        _err_s = str(_tick_err)
        if "Run not found in tenant scope" in _err_s:
            logger.warning(
                "[FullAuto][QAA v3] TickOrchestrator stale run (non-fatal): %s",
                _tick_err,
            )
        else:
            logger.error(
                f"[FullAuto][QAA v3] TickOrchestrator.run_tick 异常: {_tick_err}",
                exc_info=True,
            )
        decision = {"action": "hold", "reasoning": f"tick_error: {_tick_err}"}

    action = decision.get("action", "hold")
    llm_ready = decision.get("status") == "ready_for_llm"
    has_positions = bool(host.active_positions_cache)
    logger.info(
        f"[FullAuto][QAA v3] tick 完成: action={action}, "
        f"confidence={decision.get('confidence', 0)}, "
        f"llm_ready={llm_ready}, has_positions={has_positions}, "
        f"elapsed={time.time()-_t0:.1f}s"
    )

    # ── 根据决策执行 ──
    should_analyze = llm_ready or action in ("execute", "buy", "sell")

    if _prescreen_force_analyze and not should_analyze:
        should_analyze = True
        logger.info(
            f"[FullAuto][QAA v3][混合模式] 预筛选触发 LLM 分析 "
            f"(原 action={action}, llm_ready={llm_ready})"
        )

    # ── Tier 2A: 分级 tick（减少 LLM 调用频率）──
    # P2.3 热路径去 LLM（R1：热路径零 LLM 同步阻塞）。
    # HOTPATH_LLM_ENABLED 默认 False = 架构层安全（不依赖人工关开关）。
    # 启用时，LLM 改为异步派发（非阻塞），结果在后续 tick 作为覆盖可用；
    # 热路径永不等待 LLM（历史 60s 超时阻塞 tick 的问题根除）。
    import os as _os

    from backend.config.settings import (
        FULLAUTO_AI_DOMINANT,
        QAA_DEEP_ANALYSIS_EVERY_N_TICKS,
    )
    HOTPATH_LLM_ENABLED = _os.environ.get("HOTPATH_LLM_ENABLED", "false").lower() in (
        "1", "true", "yes", "on",
    )
    tick_num = host.unified_tick_count.get(session_id, 0)
    is_deep_tick = (
        FULLAUTO_AI_DOMINANT
        or (tick_num % QAA_DEEP_ANALYSIS_EVERY_N_TICKS == 1)
        or (QAA_DEEP_ANALYSIS_EVERY_N_TICKS <= 1)
    )
    need_llm = (is_deep_tick or should_analyze) and HOTPATH_LLM_ENABLED

    if has_positions or should_analyze:
        if need_llm:
            # P2.3: LLM 异步派发（非阻塞）。结果通过 host.llm_overlay 在后续 tick 消费。
            import threading as _threading
            def _async_llm():
                try:
                    llm_start = time.time()
                    host.run_analyst_system_v3(
                        session_id=session_id,
                        session_status=session_status,
                        session_orm_id=session_orm_id,
                        account_id=account_id,
                        active_ids=active_ids,
                        market_summary=market_summary,
                    )
                    logger.info(
                        f"[FullAuto][QAA v3] tick(#{tick_num}) 异步 LLM 完成, "
                        f"llm_elapsed={time.time()-llm_start:.1f}s"
                    )
                except Exception as _analyst_err:
                    logger.error(
                        f"[FullAuto][QAA v3] 异步 LLM 异常: {_analyst_err}",
                        exc_info=False,  # 异步路径不刷栈到热日志
                    )
            _threading.Thread(target=_async_llm, name=f"qaa-llm-async-{tick_num}", daemon=True).start()
            logger.info(
                f"[FullAuto][QAA v3] tick(#{tick_num}) LLM 已异步派发（非阻塞），"
                f"热路径继续, total_elapsed={time.time()-_t0:.1f}s"
            )
        elif has_positions:
            next_deep = ((tick_num - 1) // QAA_DEEP_ANALYSIS_EVERY_N_TICKS + 1) * QAA_DEEP_ANALYSIS_EVERY_N_TICKS + 1
            logger.info(
                f"[FullAuto][QAA v3] 快tick(#{tick_num}/{QAA_DEEP_ANALYSIS_EVERY_N_TICKS}) "
                f"跳过AI分析, total_elapsed={time.time()-_t0:.1f}s "
                f"(下次深度tick=#{next_deep})"
            )
    elif action == "cancel":
        logger.info("[FullAuto][QAA v3] decision=cancel, 检查持仓平仓需求")

    # ── Phase 3.2: 记录 EpisodicMemory ──
    try:
        if state_manager is not None:
            epi = state_manager.get_episodic_memory("trading")
            if epi is not None:
                epi.store(
                    episode_id=f"tick_{session_id}_{int(time.time())}",
                    action=action,
                    confidence=decision.get("confidence", 0),
                    context={"symbols": symbols, "reasoning": decision.get("reasoning", "")},
                )
    except Exception as _epi_err:
        logger.debug(f"[FullAuto][QAA v3] EpisodicMemory 写入失败: {_epi_err}")

    # ══════════════════════════════════════════════════════
    # Phase C: 短生命周期 session 提交结果
    # ══════════════════════════════════════════════════════
    _db_c = SessionLocal()
    host.active_db_sessions[_db_track_key] = _db_c
    try:
        session_row = _db_c.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if session_row:
            # 从 _market_scan_cache 回填编排器数据
            _orch_filled_c = 0
            for sym in symbols:
                if sym in market_summary and isinstance(market_summary[sym], dict):
                    _ms_orch = market_summary[sym].get("orchestrator")
                    if not _ms_orch or not isinstance(_ms_orch, dict) or not _ms_orch.get("long_bias"):
                        _cache_orch = (host.market_scan_cache.get(sym) or {}).get("orchestrator")
                        if _cache_orch and isinstance(_cache_orch, dict) and _cache_orch.get("long_bias"):
                            market_summary[sym]["orchestrator"] = _cache_orch
                            _orch_filled_c += 1
            if _orch_filled_c:
                logger.info(
                    f"[FullAuto][QAA v3] 编排器缓存回填(commit): {_orch_filled_c}/{len(symbols)} 个币种"
                )
            session_row.last_market_summary = market_summary
            session_row.last_health_check_at = datetime.now(timezone.utc)
            _commit_ok = host.safe_commit(_db_c, "qaa_v3_tick_c", session=session_row)
            logger.info(
                f"[FullAuto][QAA v3] session commit: ok={_commit_ok}, "
                f"orch_in_ms={any(isinstance(ms, dict) and ms.get('orchestrator', {}).get('long_bias') for ms in market_summary.values() if isinstance(ms, dict))}, "
                f"elapsed={time.time()-_t0:.1f}s"
            )
    except Exception as e:
        logger.error(f"[FullAuto][QAA v3] Phase C 提交异常: {e}", exc_info=True)
    finally:
        host.active_db_sessions.pop(_db_track_key, None)
        _db_c.close()
