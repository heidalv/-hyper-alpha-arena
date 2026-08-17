"""健康检查循环 — 从 monolith _run_health_check 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckHost:
    """monolith 状态与回调切片。"""

    active_db_sessions: Dict[str, Any]
    market_scan_cache: Dict[str, Any]
    market_scan_cache_ts: float
    last_orch_bias_by_symbol: Dict[str, str]
    last_orch_decisions: Any
    last_orch_decisions_ts: float
    last_unified_snapshot: Any
    defensive_entered_at: Dict[str, float]
    recovery_until: Dict[str, float]
    strategy_creation_ts: Dict[str, float]
    unified_tick_count: Dict[str, int]
    sub_mgr: Any
    nature_to_tier_map: Dict[str, str]
    peak_decay_grace_hours: float
    recovery_duration_hours: float
    recovery_position_scale: float
    strategy_creation_cooldown: float
    current_trace_id: str = ""
    midlong_evidence_metrics: Optional[Dict[str, Any]] = None

    purge_stale_caches: Callable = field(repr=False, default=lambda: None)
    active_exchange: Callable = field(repr=False, default=lambda: "paper")
    resolve_session_trade_symbols: Callable = field(repr=False, default=lambda *a, **k: [])
    bootstrap_market_summary: Callable = field(repr=False, default=lambda *a, **k: {})
    check_data_health: Callable = field(repr=False, default=lambda *a, **k: None)
    run_v3_factor_pipeline: Callable = field(repr=False, default=lambda *a, **k: None)
    run_with_timeout: Callable = field(repr=False, default=lambda *a, **k: None)
    orch_payload_from_decision: Callable = field(repr=False, default=lambda *a, **k: {})
    run_analyst_system: Callable = field(repr=False, default=lambda *a, **k: None)
    paper_loss_locks_disabled: Callable = field(repr=False, default=lambda *a, **k: False)
    should_terminate_strategy: Callable = field(repr=False, default=lambda *a, **k: (False, ""))
    is_champion_strategy: Callable = field(repr=False, default=lambda *a, **k: False)
    pause_champion_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    snapshot_strategy_genome: Callable = field(repr=False, default=lambda *a, **k: None)
    terminate_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    adapt_strategy_params: Callable = field(repr=False, default=lambda *a, **k: False)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    infer_timeframe_slots: Callable = field(repr=False, default=lambda *a, **k: [])
    auto_create_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    update_session_stats: Callable = field(repr=False, default=lambda *a, **k: None)
    evaluate_dynamic_risk: Callable = field(repr=False, default=lambda *a, **k: None)
    update_symbol_daily_pnl: Callable = field(repr=False, default=lambda *a, **k: None)
    live_constitutional_enabled: Callable = field(repr=False, default=lambda *a, **k: False)
    check_live_constitutional_session_risk: Callable = field(repr=False, default=lambda *a, **k: None)
    paper_auto_unlock_session: Callable = field(repr=False, default=lambda *a, **k: False)
    check_per_symbol_risk: Callable = field(repr=False, default=lambda *a, **k: None)
    should_switch_mode: Callable = field(repr=False, default=lambda *a, **k: True)
    freeze_symbol_strategies: Callable = field(repr=False, default=lambda *a, **k: None)
    unfreeze_recovered_symbols: Callable = field(repr=False, default=lambda *a, **k: None)
    evaluate_strategy_switches: Callable = field(repr=False, default=lambda *a, **k: None)
    cap_paper_active_strategies: Callable = field(repr=False, default=lambda *a, **k: False)
    ensure_market_prices: Callable = field(repr=False, default=lambda *a, **k: None)
    normalize_orchestrator_for_ui: Callable = field(repr=False, default=lambda *a, **k: None)
    attach_scalp_advisory_for_ui: Callable = field(repr=False, default=lambda *a, **k: None)
    record_strategy_pause: Callable = field(repr=False, default=lambda *a, **k: None)
    should_log_pause_event: Callable = field(repr=False, default=lambda *a, **k: True)


def build_health_check_host(svc) -> HealthCheckHost:
    return HealthCheckHost(
        active_db_sessions=svc._active_db_sessions,
        market_scan_cache=svc._market_scan_cache,
        market_scan_cache_ts=svc._market_scan_cache_ts,
        last_orch_bias_by_symbol=svc._last_orch_bias_by_symbol,
        last_orch_decisions=getattr(svc, "_last_orch_decisions", None),
        last_orch_decisions_ts=float(getattr(svc, "_last_orch_decisions_ts", 0) or 0),
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        defensive_entered_at=svc._defensive_entered_at,
        recovery_until=svc._recovery_until,
        strategy_creation_ts=svc._strategy_creation_ts,
        unified_tick_count=svc._unified_tick_count,
        sub_mgr=svc._sub_mgr,
        nature_to_tier_map=svc._NATURE_TO_TIER_MAP,
        peak_decay_grace_hours=svc._PEAK_DECAY_GRACE_HOURS,
        recovery_duration_hours=svc._RECOVERY_DURATION_HOURS,
        recovery_position_scale=svc._RECOVERY_POSITION_SCALE,
        strategy_creation_cooldown=svc._STRATEGY_CREATION_COOLDOWN,
        midlong_evidence_metrics=getattr(svc, "_midlong_evidence_metrics", None),
        purge_stale_caches=svc._purge_stale_caches,
        active_exchange=svc._active_exchange,
        resolve_session_trade_symbols=svc._resolve_session_trade_symbols,
        bootstrap_market_summary=svc._bootstrap_market_summary,
        check_data_health=svc._check_data_health,
        run_v3_factor_pipeline=svc._run_v3_factor_pipeline,
        run_with_timeout=svc._run_with_timeout,
        orch_payload_from_decision=svc._orch_payload_from_decision,
        run_analyst_system=svc._run_analyst_system,
        paper_loss_locks_disabled=svc._paper_loss_locks_disabled,
        should_terminate_strategy=svc._should_terminate_strategy,
        is_champion_strategy=svc._is_champion_strategy,
        pause_champion_strategy=svc._pause_champion_strategy,
        snapshot_strategy_genome=svc._snapshot_strategy_genome,
        terminate_strategy=svc._terminate_strategy,
        append_event=svc._append_event,
        adapt_strategy_params=svc._adapt_strategy_params,
        safe_commit=svc._safe_commit,
        get_trading_account_id=svc._get_trading_account_id,
        infer_timeframe_slots=svc._infer_timeframe_slots,
        auto_create_strategy=svc._auto_create_strategy,
        update_session_stats=svc._update_session_stats,
        evaluate_dynamic_risk=svc._evaluate_dynamic_risk,
        update_symbol_daily_pnl=svc._update_symbol_daily_pnl,
        live_constitutional_enabled=svc._live_constitutional_enabled,
        check_live_constitutional_session_risk=svc._check_live_constitutional_session_risk,
        paper_auto_unlock_session=svc._paper_auto_unlock_session,
        check_per_symbol_risk=svc._check_per_symbol_risk,
        should_switch_mode=svc._should_switch_mode,
        freeze_symbol_strategies=svc._freeze_symbol_strategies,
        unfreeze_recovered_symbols=svc._unfreeze_recovered_symbols,
        evaluate_strategy_switches=svc._evaluate_strategy_switches,
        cap_paper_active_strategies=svc._cap_paper_active_strategies,
        ensure_market_prices=svc._ensure_market_prices,
        normalize_orchestrator_for_ui=svc._normalize_orchestrator_for_ui,
        attach_scalp_advisory_for_ui=svc._attach_scalp_advisory_for_ui,
        record_strategy_pause=svc._record_strategy_pause,
        should_log_pause_event=svc._should_log_pause_event,
    )


def run_health_check(
    session_id: str,
    host: HealthCheckHost,
    *,
    maintenance_only: bool = False,
) -> None:
    import uuid as _uuid
    from backend.database.connection import SessionLocal
    from backend.database.models import FullAutoSession, AIStrategy, StrategyMemory

    host.current_trace_id = _uuid.uuid4().hex[:8]
    _hc_start = time.time()

    # 每次健康检查前清理过期缓存
    try:
        host.purge_stale_caches()
    except Exception as _pc_err:
        logger.debug(f"[FullAuto] 缓存清理异常(非致命): {_pc_err}")

    db = SessionLocal()
    # Phase 0: 注册 DB session 到追踪字典，超时时可关闭泄漏连接
    _db_track_key = f"{session_id}:health_check"
    host.active_db_sessions[_db_track_key] = db
    try:
        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session:
            return
        if session.status not in ("running", "defensive", "paused"):
            return

        if session.status == "paused":
            return

        _label = "维护巡检" if maintenance_only else "健康检查"
        logger.info(f"[FullAuto] {_label}开始: {session_id} (status={session.status})")
        # [2026-07-10 修复] run_health_check 是模块函数无 self → 直接用 host 的字段
        _ev = getattr(host, "midlong_evidence_metrics", None)
        if isinstance(_ev, dict) and int(_ev.get("samples") or 0) > 0:
            _avg = float(_ev.get("available_sum") or 0) / max(1, int(_ev["samples"]))
            logger.info(
                f"[MidLongEvidence] samples={_ev['samples']} avg_available_ratio={_avg:.2f}"
            )
        _hc_analyst_done = maintenance_only
        now = datetime.now(timezone.utc)

        # ── AI 选币过期剔除（即使 auto_coin_enabled=false 也执行）──
        auto_syms = getattr(session, "auto_coin_symbols", None) or []
        if auto_syms:
            try:
                from backend.services.auto_coin_selector import (
                    prune_expired_auto_symbols_for_session,
                )
                _expiry = prune_expired_auto_symbols_for_session(
                    db, session_id, session.account_id
                )
                if _expiry.get("removed_count", 0) > 0 or _expiry.get("renewed"):
                    db.refresh(session)
                    _symbols = list(session.symbols or [])
                    logger.info(
                        f"[FullAuto] AI 选币到期复核: 剔除 {_expiry.get('removed')} "
                        f"续期 {_expiry.get('renewed', [])} "
                        f"→ 当前 {len(_symbols)} 个交易对"
                    )
            except Exception as _exp_err:
                logger.warning(f"[FullAuto] AI 选币过期剔除失败(非致命): {_exp_err}")

        # ── D7: 预热数据中枢（主线程填充缓存，子线程直接用）──
        _symbols = host.resolve_session_trade_symbols(session, db)

        host.safe_commit(db, "hc_pre_bootstrap")

        # 1. 市场概览初值 — 先尝试从缓存读，再预热缺失的价格
        market_summary = {s: host.market_scan_cache.get(s, {"current_price":0}) for s in _symbols}
        _cached_count = sum(1 for s in _symbols if (market_summary.get(s) or {}).get('current_price'))

        if _cached_count < len(_symbols):
            # 缓存不完整，调用 _bootstrap_market_summary 获取缺失价格（含 bulk API）
            try:
                _bootstrapped = host.run_with_timeout(
                    lambda: host.bootstrap_market_summary(_symbols),
                    timeout_s=30,
                    fallback=None,
                    label="bootstrap_market_summary",
                )
                if _bootstrapped:
                    # 用引导结果覆盖 market_summary（包含实时价格）
                    market_summary.update(_bootstrapped)
                    # 同步写入缓存，后续步骤可直接使用
                    for sym, info in _bootstrapped.items():
                        if isinstance(info, dict) and info.get("current_price"):
                            host.market_scan_cache[sym] = dict(info)
                    host.market_scan_cache_ts = time.time()
                    # ★ 关键修复：价格填充后立即写入 DB，不等待整个 health check 完成
                    _price_count = sum(1 for s in _symbols if (market_summary.get(s) or {}).get('current_price'))
                    if _price_count > 0:
                        session.last_market_summary = market_summary
                        host.safe_commit(db, "hc_early_market_summary", session=session)
            except Exception as _bs_err:
                logger.warning(f"[FullAuto] 市场概览预热失败(非致命): {_bs_err}")

        logger.info(
            f"[FullAuto] 市场概览初值: "
            f"{sum(1 for s in _symbols if (market_summary.get(s) or {}).get('current_price'))}/"
            f"{len(_symbols)} 有价格"
        )

        # ── 1.0b 订单流采集：订阅用户选择的交易币种（CVD/Taker 写入 DB）──
        try:
            from services.market_flow_collector import market_flow_collector
            if _symbols and market_flow_collector.running:
                market_flow_collector.refresh_subscriptions(_symbols)
            elif _symbols and not market_flow_collector.running:
                market_flow_collector.start(symbols=_symbols)
        except Exception as _mfc_err:
            logger.debug(f"[FullAuto] 订单流订阅同步: {_mfc_err}")

        # ── 1.1 数据健康检查（断流/缺失/过期告警）──
        host.check_data_health(session, market_summary, session.symbols, db)

        # ── 统一数据快照（K线只读 DB，V3 + 编排器共用，避免重复 API/锁库）──
        _unified_snapshot = None
        try:
            from backend.services.unified_data_pool import unified_data_pool
            _unified_snapshot = unified_data_pool.capture_snapshot(
                symbols=session.symbols,
                account_id=host.get_trading_account_id(db, session),
                environment=host.active_exchange(),
                include_klines=True,
                include_strategy=True,
            )
            host.last_unified_snapshot = _unified_snapshot
            logger.info(
                f"[FullAuto] 统一快照: {len(getattr(_unified_snapshot, 'klines', {}) or {})} 组K线"
            )
            if _unified_snapshot:
                from backend.services.unified_data_pool import unified_data_pool
                unified_data_pool.merge_snapshot_into_market_summary(
                    market_summary, _unified_snapshot, session.symbols,
                )
                for _sym, _rep in (_unified_snapshot.data_completeness or {}).items():
                    if not _rep.get("ok"):
                        _miss = ",".join(_rep.get("missing") or [])
                        host.append_event(
                            session, "data_incomplete",
                            f"⚠️ {_sym} 数据快照不完整: {_miss}",
                        )
                        logger.warning(
                            f"[FullAuto] 数据不完整 {_sym}: {_miss}"
                        )
        except Exception as _snap_err:
            logger.warning(f"[FullAuto] 统一快照失败(非致命): {_snap_err}")

        # ── [V3] 因子管道（批量落库，替代逐 symbol SessionLocal）──
        factor_signal_results, regime_classifications, anomaly_reports = (
            host.run_v3_factor_pipeline(
                db, session, _symbols, market_summary,
                unified_snapshot=_unified_snapshot,
            )
        )
        for _sym, _arpt in (anomaly_reports or {}).items():
            if getattr(_arpt, "recommended_action", None) == "trade_opportunity":
                host.append_event(
                    session, "anomaly_opportunity",
                    f"{_sym}: 异常交易机会 score={_arpt.total_anomaly_score:.2f}",
                )

        # ── 1.5 多周期编排器评估（复用上方统一快照）──
        orchestrator_decisions = {}
        try:
            from backend.services.multi_timeframe_orchestrator import mt_orchestrator
            if _unified_snapshot is None:
                from backend.services.unified_data_pool import unified_data_pool
                _unified_snapshot = unified_data_pool.capture_snapshot(
                    symbols=session.symbols,
                    account_id=host.get_trading_account_id(db, session),
                    environment=host.active_exchange(),
                    include_klines=True,
                    include_strategy=True,
                )
                host.last_unified_snapshot = _unified_snapshot
            # Phase 0: 编排器评估独立超时 120s（12币种逐个评估需要较长时间）
            orchestrator_decisions = host.run_with_timeout(
                lambda: mt_orchestrator.evaluate_portfolio(
                    _symbols, _unified_snapshot
                ),
                timeout_s=120,
                fallback={},
                label="orchestrator_evaluate",
            )
            # Fix 18: 存储总控决策供中长线 agent 独立路由使用
            host.last_orch_decisions = orchestrator_decisions
            host.last_orch_decisions_ts = time.time()
            for sym, dec in orchestrator_decisions.items():
                market_summary.setdefault(sym, {})
                _orch_payload = host.orch_payload_from_decision(dec)
                try:
                    from backend.services.macro_regime_service import macro_regime_service
                    _orch_payload = macro_regime_service.inject_orchestrator_fields(
                        _orch_payload, sym,
                    )
                except Exception:
                    pass
                _orch_payload["regime"] = getattr(dec, "regime", "") or market_summary[sym].get("regime", "")
                _orch_payload["sentiment"] = dec.sentiment_index
                market_summary[sym]["orchestrator"] = _orch_payload
                # 写入顶层供方向约束和策略创建使用
                market_summary[sym]["position_bias"] = dec.long_view.bias
                market_summary[sym]["recommended_nature"] = dec.recommended_nature
                _new_bias = (dec.final_side or dec.long_view.bias or "").lower()
                _old_bias = host.last_orch_bias_by_symbol.get(sym)
                if _old_bias and _new_bias and _old_bias != _new_bias:
                    try:
                        from backend.services.trading_analysts import KlineAnalyst
                        KlineAnalyst.invalidate_symbol_cache(sym)
                        logger.info(
                            f"[FullAuto] 编排器 bias 翻转 {sym}: {_old_bias}→{_new_bias}，清除 K 线缓存"
                        )
                    except Exception:
                        pass
                if _new_bias:
                    host.last_orch_bias_by_symbol[sym] = _new_bias
            # F1-fix: 同步编排器结果到 _market_scan_cache，
            # 让 _execute_paper_trade 能读到 recommended_nature
            for sym in orchestrator_decisions:
                if sym in market_summary:
                    host.market_scan_cache.setdefault(sym, {}).update({
                        "orchestrator": market_summary[sym].get("orchestrator", {}),
                        "recommended_nature": market_summary[sym].get("recommended_nature"),
                    })
            host.market_scan_cache_ts = time.time()

            if _unified_snapshot:
                from backend.services.unified_data_pool import unified_data_pool
                unified_data_pool.merge_snapshot_into_market_summary(
                    market_summary, _unified_snapshot, session.symbols,
                )
            logger.info(
                f"[FullAuto] 编排器评估完成: "
                + ", ".join(f"{s}={d.final_action}" for s, d in orchestrator_decisions.items())
            )
        except Exception as e:
            logger.warning(f"[FullAuto] 编排器评估异常（回退原逻辑）: {e}")

        # ── 1.6 AI 决策（仅 legacy 大包健康检查；ai_first 由 _run_trading_cycle 负责）──
        _active_pre = list(session.active_strategy_ids or [])
        if (
            not maintenance_only
            and _active_pre
            and session.status in ("running", "defensive")
        ):
            try:
                logger.info(
                    f"[FullAuto] 优先执行 AI 决策: active={len(_active_pre)} "
                    f"symbols={len(session.symbols or [])}"
                )
                # SQLAlchemy Session 不能跨线程使用。_run_with_timeout 超时后
                # 子线程不会停止，继续持有 db/session 会造成 detached/事务污染。
                host.run_analyst_system(db, session, _active_pre, market_summary)
                _hc_analyst_done = True
                host.safe_commit(db, "hc_early_analyst", session=session)
            except Exception as _early_ai:
                logger.error(f"[FullAuto] 优先AI决策异常: {_early_ai}", exc_info=True)

        # ── 2. 策略评估 — 淘汰表现差的（批量查询，避免 N+1）──
        active_ids = list(session.active_strategy_ids or [])
        terminated_ids = list(session.terminated_strategy_ids or [])
        strategies_to_remove = []

        active_strats_map: Dict[str, Any] = {}
        if active_ids:
            from backend.database.models import StrategyMemory
            strats = db.query(AIStrategy).filter(
                AIStrategy.strategy_id.in_(active_ids)
            ).all()
            active_strats_map = {s.strategy_id: s for s in strats}

        for sid in active_ids:
            strat = active_strats_map.get(sid)
            if not strat:
                strategies_to_remove.append(sid)
                continue
            if strat.status == "paused":
                continue

            if host.paper_loss_locks_disabled(session):
                from backend.config.settings import PAPER_SKIP_STRATEGY_TERMINATE
                if PAPER_SKIP_STRATEGY_TERMINATE:
                    continue

            should_terminate, reason = host.should_terminate_strategy(
                db, strat, session
            )
            if should_terminate:
                mem = db.query(StrategyMemory).filter(
                    StrategyMemory.strategy_id == sid
                ).first()
                if host.is_champion_strategy(mem):
                    host.pause_champion_strategy(db, strat, reason)
                    host.append_event(
                        session, "strategy_paused",
                        f"Champion 策略 {strat.name} 暂停(保护): {reason}",
                    )
                    logger.info(f"[FullAuto] Champion 保护暂停 {sid}: {reason}")
                else:
                    host.snapshot_strategy_genome(db, strat, mem)
                    host.terminate_strategy(db, strat, reason)
                    strategies_to_remove.append(sid)
                    terminated_ids.append(sid)
                    host.append_event(session, "strategy_terminated",
                                       f"策略 {strat.name} 被终止: {reason}")
                    logger.info(f"[FullAuto] 终止策略 {sid}: {reason}")

        for sid in strategies_to_remove:
            if sid in active_ids:
                active_ids.remove(sid)

        # ── 2.5 策略参数自适应 — 根据市场+表现动态调整杠杆/仓位/止损 ──
        adapted_count = 0
        for sid in active_ids:
            strat = active_strats_map.get(sid)
            if not strat or strat.status != "active":
                continue
            sym = strat.primary_symbol or ""
            mkt_info = market_summary.get(sym, {})
            try:
                if host.adapt_strategy_params(db, strat, mkt_info):
                    adapted_count += 1
            except Exception as adapt_err:
                logger.warning(f"[FullAuto] 策略自适应异常 {sid}: {adapt_err}")
        if adapted_count:
            host.safe_commit(db, "strategy_adaptation")
            host.append_event(session, "strategy_adapted",
                f"自适应调整了 {adapted_count} 个策略的风控参数")

        # ── 3. 策略编排（per-symbol × per-tier 多周期覆盖）──
        # 【关键】直接查数据库获取当前 active/paused 策略，而非依赖 session 列表
        # 这样即使 session.active_strategy_ids 因并发写入丢失 ID，也不会重复创建
        n_symbols = len(session.symbols or [])
        configured_max = min(int(session.max_concurrent_strategies or 25), 15)
        try:
            from backend.services.training_phase_service import is_active, max_active_strategies
            if is_active():
                max_strats = max_active_strategies()
            else:
                max_strats = min(max(configured_max, n_symbols * 2), 15)
        except Exception:
            max_strats = min(max(configured_max, n_symbols * 2), 15)

        # P5-fix(2026-05-08): paper 模式下策略落在 paper_account_id，
        # 用 _get_trading_account_id 取实际持仓账户的策略
        _strat_acct_id = host.get_trading_account_id(db, session)
        db_live_strats = db.query(AIStrategy).filter(
            AIStrategy.account_id == _strat_acct_id,
            AIStrategy.primary_symbol.in_(session.symbols),
            AIStrategy.status.in_(["active", "paused"]),
        ).all()

        existing_symbol_map: Dict[str, List[str]] = {}
        existing_symbol_tier_set: set = set()
        live_active_ids: list = []
        for strat in db_live_strats:
            sym = strat.primary_symbol or ""
            existing_symbol_map.setdefault(sym, []).append(strat.strategy_id)
            live_active_ids.append(strat.strategy_id)
            # 优先用 timeframe_tier，回退到 genome.trade_nature 推断，最后用 "mid"
            _tier = getattr(strat, 'timeframe_tier', None)
            if not _tier:
                _genome = getattr(strat, 'genome', None) or {}
                _nature = _genome.get('trade_nature', '') if isinstance(_genome, dict) else ''
                _tier = host.nature_to_tier_map.get(_nature, 'mid') if _nature else 'mid'
            existing_symbol_tier_set.add(f"{sym}:{_tier}")

        # 同步 session 的 active_strategy_ids（以 DB 为准）
        active_ids = live_active_ids
        session.active_strategy_ids = active_ids
        session.terminated_strategy_ids = terminated_ids
        host.safe_commit(db, "pre_strategy_creation")
        db.refresh(session)

        logger.debug(
            f"[FullAuto] 策略编排: {len(db_live_strats)} 个活跃策略, "
            f"已有 {len(existing_symbol_tier_set)} 个 symbol:tier 组合, "
            f"上限 {max_strats}"
        )

        for symbol in session.symbols:
            orch_dec = orchestrator_decisions.get(symbol)
            mkt_info = market_summary.get(symbol, {})

            if orch_dec:
                from backend.services.multi_timeframe_orchestrator import mt_orchestrator as _mto
                mkt_info["orchestrator_params"] = _mto.to_strategy_params(orch_dec)
                recommended = orch_dec.recommended_slots

                # 二次矛盾检测：仅当编排器自身判定 wait/frozen 时才拦截
                # 编排器已通过加权投票解决了方向矛盾，不再重复拦截
                if orch_dec.final_action in ("wait", "frozen"):
                    _biases = []
                    for _v in [orch_dec.long_view, orch_dec.mid_view, orch_dec.short_view]:
                        if _v.bias in ("bullish", "bearish"):
                            _biases.append(_v.bias)
                    _has_bull = any(b == "bullish" for b in _biases)
                    _has_bear = any(b == "bearish" for b in _biases)
                    if _has_bull and _has_bear:
                        recommended = []
                        host.append_event(session, "orchestrator_decision",
                            f"{symbol}: ⛔ 编排器矛盾+wait 二次确认拦截 — "
                            f"L={orch_dec.long_view.bias}/M={orch_dec.mid_view.bias}/S={orch_dec.short_view.bias}")
                        logger.warning(
                            f"[FullAuto] {symbol} 编排器矛盾+wait 二次确认: "
                            f"L={orch_dec.long_view.bias}/M={orch_dec.mid_view.bias}/S={orch_dec.short_view.bias} "
                            f"(final_action={orch_dec.final_action})"
                        )
            else:
                recommended = []
                host.append_event(
                    session, "orchestrator_unavailable",
                    f"{symbol}: 编排器暂无有效结果，跳过周期推荐/策略创建",
                )
                logger.warning(
                    f"[FullAuto] {symbol}: 编排器无结果，禁止默认 short/mid/long 推荐"
                )

            if orch_dec and orch_dec.final_action == "frozen" and not recommended:
                _reason = getattr(orch_dec, 'reasoning', '') or orch_dec.final_action
                host.append_event(session, "orchestrator_decision",
                    f"{symbol}: ⛔ {_reason}")
                recommended = []
            elif orch_dec and orch_dec.final_action == "wait" and not recommended:
                # wait + 无推荐 = 观望，但不断言失败
                # 可解释化：标注「为什么观望」——三周期方向/置信度 + 当前成熟度阶段，
                # 替代旧的含糊「无明确信号」（用户无法判断是方向中性、分歧还是门槛拦截）。
                try:
                    _ms = ""
                    from backend.services.maturity_controller import get_global_stage
                    _ms = f" 成熟度={get_global_stage()}"
                except Exception:
                    _ms = ""
                _lv, _mv, _sv = orch_dec.long_view, orch_dec.mid_view, orch_dec.short_view
                _wait_why = (
                    f"L={_lv.bias}({float(getattr(_lv,'confidence',0) or 0)*100:.0f}%)/"
                    f"M={_mv.bias}({float(getattr(_mv,'confidence',0) or 0)*100:.0f}%)/"
                    f"S={_sv.bias}({float(getattr(_sv,'confidence',0) or 0)*100:.0f}%)"
                )
                host.append_event(session, "orchestrator_decision",
                    f"{symbol}: 编排器观望（三周期方向中性/分歧，非门槛拦截）{_ms} {_wait_why}")
            else:
                if orch_dec:
                    host.append_event(session, "orchestrator_decision",
                        f"{symbol}: 推荐[{','.join(recommended)}] "
                        f"L={orch_dec.long_view.bias}/"
                        f"M={orch_dec.mid_view.bias}/"
                        f"S={orch_dec.short_view.bias}")

            # ── 构建各 tier 独立信号（与快评阶段一致）──
            _tier_signal = {}
            if orch_dec:
                for _tv_key, _tv_obj in [("short", orch_dec.short_view), ("mid", orch_dec.mid_view), ("long", orch_dec.long_view)]:
                    _tv_bias = getattr(_tv_obj, "bias", "neutral")
                    _tv_conf = float(getattr(_tv_obj, "confidence", 0) or 0)
                    _tv_has_signal = _tv_bias not in ("neutral",) and _tv_conf >= 0.20
                    _tier_signal[_tv_key] = {"bias": _tv_bias, "conf": _tv_conf, "has_signal": _tv_has_signal}
            _is_frozen_sym = orch_dec and orch_dec.final_action == "frozen"
            _all_neutral_sym = all(ts["bias"] == "neutral" for ts in _tier_signal.values()) if _tier_signal else True
            # 2026-06-17: 删除 _sym_has_pos 死代码块（原 3053-3064）。
            # 该变量查 paper_engine.get_positions 后从未被任何后续逻辑读取
            # （grep 全文仅 3054/3058 两处，均为赋值），却每个 symbol 都触发一次
            # 全量持仓 DB 查询，纯属开销。风险冻结判定由 _is_frozen_sym 负责。
            # 仅风险冻结才暂停策略和跳过创建；三周期无信号不暂停（让LLM决定）
            _unified_pause_sym = _is_frozen_sym
            
            should_run = len(recommended) > 0
            existing_sids = existing_symbol_map.get(symbol, [])
            
            # ── 按 tier 独立处理已有策略的暂停/恢复 ──
            # 核心原则：只要不是“风险冻结”，就保持策略 active，让 LLM 自己决定是否交易
            _wait_tiers = []
            for esid in existing_sids:
                es = db.query(AIStrategy).filter(
                    AIStrategy.strategy_id == esid).first()
                if not es:
                    continue
            
                # 确定该策略的 tier
                _es_tier = (
                    getattr(es, 'timeframe_tier', None)
                    or host.nature_to_tier_map.get(
                        (es.genome or {}).get("trade_nature", ""), "mid")
                    if es.genome else "mid"
                )
            
                if _is_frozen_sym:
                    # 风险冻结：暂停所有策略
                    if es.status == "active":
                        es.status = "paused"
                        db.flush()
                        if esid in active_ids:
                            active_ids.remove(esid)
                        host.record_strategy_pause(esid, "风险冻结", by="full_tick")
                        # 2026-06-19: 统一注册到 SymbolLockRegistry
                        from backend.services.symbol_lock_registry import lock_registry
                        lock_registry.lock(symbol, strategy_id=esid,
                                           reason_code="orchestrator_frozen", by="full_tick")
                        if host.should_log_pause_event(session.session_id, f"pause:{esid}:risk"):
                            host.append_event(session, "strategy_paused",
                                f"{symbol}/{_es_tier} 风险冻结 → 策略暂停")
                        logger.info(f"[FullAuto] 暂停 {symbol}/{_es_tier} 策略: 风险冻结")
                else:
                    # 非风险冻结：不在此恢复 paused（由快评/解冻逻辑负责，避免与震荡市暂停打架）
                    if es.status == "active" and esid not in active_ids:
                        active_ids.append(esid)
            
            
            if _is_frozen_sym and not should_run:
                # 风险冻结 + 无推荐 → 跳过策略创建
                continue
            
            if not should_run and not existing_sids:
                # 无推荐且无已有策略 → 不创建
                continue

            if should_run:
                try:
                    from backend.config.settings import STRICT_DATA_GATE
                    if STRICT_DATA_GATE:
                        from backend.services.data_readiness_gate import assess_symbol_data
                        _snap_gate = getattr(self, "_last_unified_snapshot", None)
                        _rep_gate = assess_symbol_data(
                            symbol,
                            snapshot=_snap_gate,
                            market_info=market_summary.get(symbol, {}),
                        )
                        if not _rep_gate.trading_ready:
                            should_run = False
                            host.append_event(
                                session, "data_gate_block",
                                f"🚫 {symbol} 数据未就绪，禁止新开仓/新策略: "
                                f"{_rep_gate.summary()}",
                            )
                            logger.warning(
                                f"[FullAuto] 数据门控拦截新开 {symbol}: "
                                f"{_rep_gate.summary()}"
                            )
                except Exception as _sg_err:
                    logger.debug(f"[FullAuto] 策略创建门控: {_sg_err}")

            if should_run:
                # F1-5: 使用 reentry_cooldown 按 tier 隔离检查冷却
                _all_tiers_cooling = False
                try:
                    from backend.services.reentry_cooldown import reopen_blocked
                    _tier_check = {"short", "mid", "long"}
                    _tier_results = []
                    _cool_acct = host.get_trading_account_id(db, session)
                    for _t in _tier_check:
                        _blocked, _reason = reopen_blocked(
                            _cool_acct, symbol, "buy", new_tier=_t
                        )
                        _tier_results.append(_blocked)
                    _all_tiers_cooling = all(_tier_results)
                except Exception as _cd_err:
                    logger.debug(f"[FullAuto] reentry_cooldown 检查失败: {_cd_err}")
                    _all_tiers_cooling = False

                if _all_tiers_cooling:
                    logger.info(
                        f"[FullAuto] {symbol} 全tier冷却中，跳过策略创建"
                    )
                    # 深挖第 3 项 (2026-05-08)：guard 拦截事件统一持久化
                    try:
                        from backend.services.unified_risk_gate import record_guard_block
                        record_guard_block(
                            db, account_id=host.get_trading_account_id(db, session),
                            guard_name="reentry_cooldown",
                            symbol=symbol, side="buy",
                            reason="全 tier (short/mid/long) 都在再开冷却中",
                            extra={"point": "strategy_creation"},
                        )
                    except Exception:
                        pass
                    continue

                # 【同步】多周期策略创建：为缺失的 tier 创建新策略
                # 优先使用 orchestrator 推荐的 slots，回退到 _infer_timeframe_slots
                if orch_dec and orch_dec.recommended_slots:
                    needed_slots = orch_dec.recommended_slots
                else:
                    needed_slots = host.infer_timeframe_slots(mkt_info)
                for _slot in needed_slots:
                    _sym_tier_key = f"{symbol}:{_slot}"
                    if _sym_tier_key in existing_symbol_tier_set:
                        continue
                    # 创建冷却检查：同一 symbol:tier 至少间隔 10 分钟
                    _last_create = host.strategy_creation_ts.get(_sym_tier_key, 0)
                    if time.time() - _last_create < host.strategy_creation_cooldown:
                        logger.info(f"[FullAuto] {_sym_tier_key} 创建冷却中，跳过")
                        continue
                    if len(active_ids) >= max_strats:
                        logger.warning(f"[FullAuto] 达到策略上限 {max_strats}，停止创建")
                        break
                    reason = (orch_dec.slot_reasoning.get(
                        _slot, f"多周期覆盖-{_slot}")
                        if orch_dec else f"默认创建-{_slot}")
                    new_id = host.auto_create_strategy(
                        None, None, symbol, {**mkt_info, "_force_slot": _slot},
                        _account_id=host.get_trading_account_id(db, session),
                        _risk_level=session.risk_level,
                        _trading_mode=session.trading_mode,
                        _symbols=list(session.symbols or [])
                    )
                    if new_id:
                        active_ids.append(new_id)
                        existing_symbol_tier_set.add(_sym_tier_key)
                        host.strategy_creation_ts[_sym_tier_key] = time.time()
                        host.append_event(session, "strategy_created",
                            f"为 {symbol}/{_slot} 创建策略: {reason}")
                        logger.info(f"[FullAuto] 同步创建 {symbol}/{_slot} 策略: {new_id}")
                        # ── 整改项4: 新策略继承历史经验 ────────────────────
                        try:
                            from backend.services.position_memory_manager import inherit_strategy_memory
                            _inherited = inherit_strategy_memory(db, symbol, _slot)
                            if _inherited.get("key_lessons") or _inherited.get("successful_patterns"):
                                from backend.database.models import StrategyMemory as _SM
                                _mem = db.query(_SM).filter(
                                    _SM.strategy_id == new_id
                                ).first()
                                if _mem:
                                    if _inherited.get("key_lessons") and not _mem.key_lessons:
                                        _mem.key_lessons = _inherited["key_lessons"]
                                    if _inherited.get("successful_patterns"):
                                        _mem.successful_patterns = _inherited["successful_patterns"]
                                    if _inherited.get("failed_patterns"):
                                        _mem.failed_patterns = _inherited["failed_patterns"]
                                    db.flush()
                                    logger.info(
                                        f"[FullAuto] 新策略 {new_id[:8]} 继承记忆: "
                                        f"lessons={len(_inherited.get('key_lessons') or [])} "
                                        f"patterns={len(_inherited.get('successful_patterns') or [])}"
                                    )
                                else:
                                    _new_mem = _SM(
                                        strategy_id=new_id,
                                        key_lessons=_inherited.get("key_lessons"),
                                        successful_patterns=_inherited.get("successful_patterns"),
                                        failed_patterns=_inherited.get("failed_patterns"),
                                        total_trades=0,
                                    )
                                    db.add(_new_mem)
                                    db.flush()
                                    logger.info(
                                        f"[FullAuto] 新策略 {new_id[:8]} 创建记忆并继承 "
                                        f"{len(_inherited.get('key_lessons') or [])} 条经验"
                                    )
                        except Exception as e:
                            logger.warning(f"[FullAuto] 经验继承异常(非致命): {e}")
                    else:
                        logger.warning(f"[FullAuto] {symbol}/{_slot} 策略创建失败")
                # 更新 session
                session.active_strategy_ids = active_ids
                host.safe_commit(db, f"strategy_creation_{symbol}")

        # 策略创建/恢复/暂停完成，中间 commit 释放 DB 锁
        session.active_strategy_ids = active_ids
        session.terminated_strategy_ids = terminated_ids
        host.safe_commit(db, "post_strategy_orchestration")
        db.refresh(session)

        # ── 3.5 策略库匹配（混合路线核心）──
        # 为每个 symbol×tier 匹配最佳模板并计算技术信号
        # 信号注入 market_summary，供分析师/LLM 审核（而非从零发明）
        try:
            from backend.services.strategy_library import strategy_library
            from backend.services.market_data import get_kline_data
            import pandas as pd

            strategy_library.load_templates(db)
            _template_signals_count = 0
            _template_strategies_created = 0

            for _sym in session.symbols:
                _regime_info = regime_classifications.get(_sym)
                _regime_str = "ranging"
                if _regime_info is not None:
                    _raw_regime = (
                        getattr(_regime_info, "regime", None)
                        or (_regime_info.get("regime") if isinstance(_regime_info, dict) else None)
                        or "ranging"
                    )
                    if hasattr(_raw_regime, "value"):
                        _regime_str = str(_raw_regime.value)
                    else:
                        _regime_str = str(_raw_regime)

                _mkt = market_summary.get(_sym, {})
                _price = _mkt.get("current_price", 0) if isinstance(_mkt, dict) else 0

                for _tier in ["short", "mid", "long"]:
                    try:
                        _matches = strategy_library.match(
                            db, _regime_str, symbol=_sym, tier=_tier
                        )
                        if not _matches:
                            continue

                        # P5-fix(2026-05-08): 取 top 3 模板而非只取第一名
                        # 单一模板信号会让 AI 视野过窄 — 比如 ranging 下只看 range 模板
                        # 必然 HOLD，错过 trend/momentum 模板可能给出的 BUY/SELL。
                        # 多模板并列展示让 AI 自己权衡共识 vs 分歧。
                        _top_matches = [m for m in _matches[:3] if m.confidence >= 0.30]
                        if not _top_matches:
                            continue

                        # 用首位模板（最佳匹配）作为后续创建策略的依据
                        _best = _top_matches[0]
                        _best_signal = None  # 用于 Step 3 创建策略

                        for _m in _top_matches:
                            # 获取 K 线数据
                            _tf = _m.timeframe or "1h"
                            _count = max(120, int(_m.signal_params.get("ema_slow", 55)) + 30)
                            _raw = get_kline_data(_sym, period=_tf, count=_count)
                            if not _raw or len(_raw) < 20:
                                continue

                            _kdf = pd.DataFrame(_raw)
                            _signal_m = strategy_library.compute_signals(
                                _m, _kdf, current_price=_price
                            )
                            if _m.template_id == _best.template_id:
                                _best_signal = _signal_m

                            # 存储到 market_summary（供 LLM prompt 使用）
                            _mkt = market_summary.setdefault(_sym, {})
                            _tpl_signals = _mkt.setdefault("template_signals", [])
                            _tpl_row = strategy_library.get_template_by_id(db, _m.template_id) or {}
                            _tags = list(_tpl_row.get("tags") or [])
                            _live = _tpl_row.get("_live_stats") or {}
                            _live_wr = (
                                _live.get("wins", 0) / _live.get("total_trades", 1)
                                if _live.get("total_trades") else None
                            )
                            _is_verified = (
                                _tpl_row.get("source") == "promoted"
                                or bool(_tpl_row.get("verified_at"))
                                or "champion" in [t.lower() for t in _tags]
                                or "实战验证" in _tags
                            )
                            _tpl_signals.append({
                                "template_id": _m.template_id,
                                "template_name": _m.template_name,
                                "category": _m.category,
                                "tier": _tier,
                                "timeframe": _tf,
                                "match_confidence": _m.confidence,
                                "direction": _signal_m.direction,
                                "signal_confidence": _signal_m.confidence,
                                "reason": _signal_m.reason,
                                "score": _signal_m.score,
                                "risk_params": _m.risk_params,
                                "match_reason": _m.match_reason,
                                "tags": _tags,
                                "verified": _is_verified,
                                "source": _tpl_row.get("source") or "builtin",
                                "live_win_rate": round(_live_wr * 100, 1) if _live_wr is not None else None,
                                "backtest_win_rate": (
                                    round(float(_tpl_row.get("_orm").backtest_win_rate or 0) * 100, 1)
                                    if _tpl_row.get("_orm") and _tpl_row.get("_orm").backtest_win_rate
                                    else None
                                ),
                            })
                            _template_signals_count += 1

                        # ── Step 3: 为高置信度模板创建 AIStrategy 记录 ──
                        # P5-fix: 用 _best_signal 而非循环外泄漏的变量
                        if _best_signal and _best_signal.direction != "hold" and _best.confidence >= 0.45:
                            try:
                                from backend.database.models import AIStrategy as _AIStrat
                                # P5-fix(2026-05-08): paper模式必须用 paper_account_id
                                _strat_acct = host.get_trading_account_id(db, session)
                                _existing = db.query(_AIStrat).filter(
                                    _AIStrat.account_id == _strat_acct,
                                    _AIStrat.primary_symbol == _sym,
                                    _AIStrat.status.in_(["active", "paused"]),
                                ).all()
                                _has_template = any(
                                    (s.genome or {}).get("source_template_id") == _best.template_id
                                    for s in _existing
                                )
                                if not _has_template:
                                    _new_sid = strategy_library.create_strategy_from_template(
                                        db, _best.template_id,
                                        _strat_acct, _sym
                                    )
                                    if _new_sid:
                                        _template_strategies_created += 1
                                        if _new_sid not in active_ids:
                                            active_ids.append(_new_sid)
                                        _sym_tier_key = f"{_sym}:{_tier}"
                                        existing_symbol_tier_set.add(_sym_tier_key)
                            except Exception as _cre_err:
                                logger.debug(f"[FullAuto] 策略库策略创建跳过 {_sym}/{_tier}: {_cre_err}")
                    except Exception as _tier_err:
                        logger.debug(f"[FullAuto] 模板匹配异常 {_sym}/{_tier}: {_tier_err}")

            if _template_signals_count > 0:
                logger.info(
                    f"[FullAuto] 策略库匹配: {_template_signals_count} 个模板信号 "
                    f"覆盖 {len(session.symbols)} symbols"
                    + (f", 创建 {_template_strategies_created} 个新策略" if _template_strategies_created else "")
                )
            if _template_strategies_created > 0:
                host.safe_commit(db, "template_strategies_created")
        except Exception as _sl_err:
            logger.warning(f"[FullAuto] 策略库匹配跳过: {type(_sl_err).__name__}: {_sl_err}")

        # ── 4. 先更新绩效（确保风控用最新数据）──
        host.update_session_stats(db, session, active_ids)

        # ── 4.5 AI 动态风险评估 ──
        host.evaluate_dynamic_risk(session, market_summary)

        # ── 4.55 per-symbol 日亏损追踪 ──
        host.update_symbol_daily_pnl(db, session)

        # ── 4.56 P0-E 分层熔断：周期级日亏预算（只冻本 tier，绝不跨周期）──
        try:
            from backend.services.tier_circuit_breaker import (
                check_and_update as _tier_cb_update,
                get_tier_circuit_snapshot as _tier_cb_snapshot,
            )
            _tier_acct = host.get_trading_account_id(db, session)
            if _tier_acct:
                _tier_before = {
                    t: bool(s.get("frozen"))
                    for t, s in _tier_cb_snapshot(_tier_acct).items()
                }
                _tier_after = _tier_cb_update(db, _tier_acct)
                for _t in ("short", "mid", "long"):
                    _was_frozen = _tier_before.get(_t, False)
                    _now_frozen = bool((_tier_after.get(_t) or {}).get("frozen"))
                    if _now_frozen and not _was_frozen:
                        host.append_event(session, "tier_circuit_trigger",
                            f"🚧 周期熔断触发[{_t}]: {(_tier_after[_t] or {}).get('reason', '')}")
                    elif _was_frozen and not _now_frozen:
                        host.append_event(session, "tier_circuit_release",
                            f"✅ 周期熔断解除[{_t}]（日亏预算跨日重置）")
        except Exception as _tier_cb_err:
            logger.debug("[FullAuto] tier_circuit_breaker 巡检跳过: %s", _tier_cb_err)

        # ── 4.6 风控巡检 (per-symbol + 全局极端安全网) ──
        if host.live_constitutional_enabled(session):
            host.check_live_constitutional_session_risk(db, session)
        elif host.paper_loss_locks_disabled(session):
            host.paper_auto_unlock_session(db, session)
        else:
            risk_result = host.check_per_symbol_risk(db, session)

            if risk_result.global_freeze:
                # 全局极端安全网触发：回撤>50% 或日亏损>15% → 整个 session 进入 defensive
                if session.status != "defensive":
                    if not host.should_switch_mode(session_id, session.status, "defensive"):
                        logger.info(f"[FullAuto] 进入防守被缓冲延迟 {session_id}")
                    else:
                        host.defensive_entered_at[session_id] = time.time()
                        hours_hint = f" | 峰值衰减将在{host.peak_decay_grace_hours}h后启动"
                        host.append_event(session, "circuit_breaker",
                            f"[WARN] 进入防守模式(极端安全网): {risk_result.global_reason} | 暂停开新仓{hours_hint}")
                        logger.warning(f"[FullAuto] 进入防守模式 {session_id}: {risk_result.global_reason}")
                        session.status = "defensive"
                        session.pause_reason = "circuit_breaker"
                        host.invalidate_session_status_cache(session_id)
                        # [2026-08-15 收敛] 会话级熔断事件统一登记（真全局风险场景，保留语义）
                        try:
                            from backend.services.risk_management.freeze_coordinator import register_event
                            register_event("freeze_circuit_breaker",
                                           int(getattr(session, "account_id", 0) or 0),
                                           "session", str(session_id),
                                           str(risk_result.global_reason or "")[:160])
                        except Exception:
                            pass
            else:
                # per-symbol 冻结：仅冻结亏损 symbol，其他正常交易
                for _fz_sym in risk_result.frozen_symbols:
                    _fz_reason = risk_result.symbol_reasons.get(_fz_sym, "未知原因")
                    # P0-E: tier 归因可用时只冻结亏损所属周期
                    _fz_tiers = risk_result.frozen_symbol_tiers.get(_fz_sym) or None
                    host.freeze_symbol_strategies(db, session, _fz_sym, _fz_reason,
                                                  tiers=_fz_tiers)

                # 解冻已恢复的 symbol
                host.unfreeze_recovered_symbols(db, session, risk_result.frozen_symbols)

                # 如果之前在 defensive 但现在不在极端安全网 → 退出防守
                if session.status == "defensive":
                    if not host.should_switch_mode(session_id, "defensive", "running"):
                        logger.info(f"[FullAuto] 退出防守被缓冲延迟 {session_id}")
                    else:
                        entered = host.defensive_entered_at.pop(session_id, None)
                        defensive_hours = (time.time() - entered) / 3600 if entered else 0
                        host.recovery_until[session_id] = time.time() + host.recovery_duration_hours * 3600
                        session.status = "running"
                        session.pause_reason = None
                        dd_pct = (getattr(session, 'current_drawdown', 0) or 0) * 100
                        host.append_event(session, "defensive_exit",
                            f"[OK] 退出防守模式(持续{defensive_hours:.1f}h)，进入{host.recovery_duration_hours}h恢复期"
                            f"(仓位{host.recovery_position_scale:.0%}) | DD={dd_pct:.1f}%")
                        logger.info(f"[FullAuto] 退出防守模式 {session_id}, "
                                    f"防守{defensive_hours:.1f}h, 恢复期{host.recovery_duration_hours}h")
                        host.invalidate_session_status_cache(session_id)

        # ── 4.7 智能策略切换评估 ──
        if session.status == "running":
            host.evaluate_strategy_switches(db, session, active_ids)

        # 提交风险巡检 + 策略切换产生的事件（避免后续长耗时 AI 调用失败导致全部丢失）
        host.safe_commit(db, "hc_pre_analyst", session=session)

        # ── 5. 核心：多路分析师（若 1.6 已跑则跳过，避免重复耗 LLM）──
        if not _hc_analyst_done:
            # 不用 _run_with_timeout：它会把同一个 SQLAlchemy Session 传到子线程，
            # 超时后线程仍运行，主线程继续 rollback/close 会造成 detached 对象。
            host.run_analyst_system(db, session, active_ids, market_summary)

        session.active_strategy_ids = active_ids
        session.terminated_strategy_ids = terminated_ids
        if not host.safe_commit(db, "health_check", session=session):
            logger.error(f"[FullAuto] 健康检查最终提交失败！session={session_id}")

        # ── 子仓位对账（每次健康检查时运行）──
        if host.sub_mgr:
            try:
                # P5-fix(2026-05-08): paper 模式对账要看 paper_account 的子仓
                account_id = host.get_trading_account_id(db, session)

                # 阶段 3: 拉取交易所实际净仓位用于对账（live 模式）。
                # 失败则回退 0（保持旧行为：跳过 exchange_qty 比对）。
                _exchange_qty_by_sym: Dict[str, float] = {}
                _exchange_lev_by_sym: Dict[str, float] = {}
                _session_is_live = False
                try:
                    _session_is_live = (
                        str(getattr(session, "trading_mode", "") or "").lower() == "live"
                    )
                except Exception:
                    _session_is_live = False

                if _session_is_live:
                    try:
                        from backend.services.exchange.live_executor import LiveExecutor
                        _le = LiveExecutor()
                        _live_positions = _le.get_positions(db, account_id, status="open")
                        for _p in (_live_positions or []):
                            _psym = (_p.get("symbol") or "").upper()
                            if not _psym:
                                continue
                            _sz = float(_p.get("size", 0) or 0)
                            _side = str(_p.get("side", "") or "").lower()
                            # signed: long 正 / short 负
                            _exchange_qty_by_sym[_psym] = (
                                _sz if _side == "long" else -_sz
                            )
                            _exchange_lev_by_sym[_psym] = float(_p.get("leverage", 1) or 1)
                    except Exception as _ex_err:
                        logger.debug(f"[FullAuto] 交易所仓位拉取失败，回退 0: {_ex_err}")

                import os as _os
                _live_sub_tracking = _os.getenv(
                    "LIVE_SUB_POSITION_TRACKING", "false"
                ).lower().strip() in ("true", "1", "yes", "on")

                for sym in (session.symbols or []):
                    _ex_qty = float(_exchange_qty_by_sym.get(sym.upper(), 0) or 0)
                    _ex_lev = float(_exchange_lev_by_sym.get(sym.upper(), 1) or 1)
                    recon = host.sub_mgr.reconcile(
                        db, account_id, sym,
                        exchange_qty=_ex_qty,
                    )
                    if not recon.get("matched", True):
                        host.append_event(session, "reconcile_mismatch",
                            f"[WARN] {sym} 子仓对账不一致: "
                            f"内部={recon['internal_qty']:.6f} "
                            f"交易所={_ex_qty:.6f} "
                            f"natures={recon['natures']}")

                    # 阶段 3: live 子仓位账本对账（LiveSubPosition vs 交易所）
                    if _live_sub_tracking:
                        try:
                            from backend.services.live_position_manager import (
                                live_position_manager,
                            )
                            _lpm_recon = live_position_manager.reconcile(
                                db, account_id, sym,
                                exchange_qty=_ex_qty,
                                exchange_leverage=_ex_lev,
                            )
                            if not _lpm_recon.get("matched", True):
                                host.append_event(
                                    session, "live_reconcile_mismatch",
                                    f"[WARN] {sym} live 子仓对账不一致: "
                                    f"本地={_lpm_recon.get('local', 0):.6f} "
                                    f"交易所={_lpm_recon.get('exchange', 0):.6f} "
                                    f"差额={_lpm_recon.get('diff', 0):.6f}",
                                )
                        except Exception as _lpm_err:
                            logger.debug(
                                f"[FullAuto] live 子仓对账跳过 {sym}: {_lpm_err}"
                            )
            except Exception as _rec_err:
                logger.debug(f"[FullAuto] 子仓对账跳过: {_rec_err}")

        # ── 策略健康评估 + 自修复 (StrategyHealthService) ──
        # 模拟盘：每隔一个 tick 评估，减轻 DB/CPU 压力
        _hc_tick = int(host.unified_tick_count.get(session_id, 0) or 0)
        _run_strategy_health = (_hc_tick % 2 == 0)
        if _run_strategy_health:
            try:
                from backend.services.strategy_health_service import get_strategy_health_service
                _health_svc = get_strategy_health_service()
                _healed_count = 0
                for sid in list(active_ids):
                    h_report = _health_svc.evaluate_strategy_health(
                        strategy_id=sid, db=db, strategy=None,
                        market_summary=market_summary,
                    )
                    if h_report.level.value not in ("healthy",):
                        logger.info(
                            f"[FullAuto] 策略健康异常 {sid}: "
                            f"level={h_report.level.value}, action={h_report.recommended_action.value}"
                        )
                        heal_result = _health_svc.auto_heal(sid, h_report, db=db)
                        if heal_result.get("applied"):
                            _healed_count += 1
                            host.append_event(session, "strategy_health_heal",
                                f"策略 {sid}: {heal_result['action']} "
                                f"(level={h_report.level.value})")
                if _healed_count:
                    host.safe_commit(db, "strategy_health_heal", session=session)
                    logger.info(f"[FullAuto] 策略健康自修复: {_healed_count} 个策略被调整")
            except Exception as _health_err:
                logger.debug(f"[FullAuto] 策略健康评估跳过: {_health_err}")

        if host.paper_loss_locks_disabled(session):
            if host.cap_paper_active_strategies(db, session, active_ids):
                session.active_strategy_ids = active_ids
                host.safe_commit(db, "paper_strategy_cap", session=session)

        # ── 灰度发布计划评估（确认/回滚）──
        try:
            from backend.services.qaa_evolution_bridge import qaa_bridge
            if qaa_bridge._enabled:
                qaa_bridge.check_grayscale_plans(db)
        except Exception:
            pass

        # ── [2026-08-17 专职退出 Agent] 跨 tier 退出协调与风控诊断 ──
        # 时间止损/同向叠加预警（默认仅建议，EXIT_AGENT_EXECUTE=true 才执行）。
        try:
            from backend.services.full_auto.exit_agent import run_exit_pass as _exit_pass
            from backend.services.paper_trading_engine import paper_engine
            _exit_acct_id = host.get_trading_account_id(db, session)
            _open_pos = paper_engine.get_positions(db, _exit_acct_id, status="open") or []
            if _open_pos:
                _exit_pass(db, _open_pos, market_summary)
        except Exception as _exit_agent_err:
            logger.debug("[FullAuto] ExitAgent 巡检跳过: %s", _exit_agent_err)

        # ── [2026-08-17 因果回灌闭环] 每小时重建亏损模式约束（文件时间戳节流）──
        # piggyback 在健康检查上：读 data/causal_constraints.json 的 updated_at，
        # 超过 1h 才重建，避免每个 tick 全表扫 trade_facts。
        try:
            from backend.services.full_auto import causal_feedback as _cf
            _cf_path = os.path.join(os.getcwd(), _cf.CONSTRAINTS_PATH)
            _cf_last = 0.0
            try:
                with open(_cf_path, "r", encoding="utf-8") as _f:
                    _cf_last = float(json.load(_f).get("updated_at") or 0)
            except Exception:
                pass
            if time.time() - _cf_last >= 3600:
                _cf.rebuild(db)
        except Exception as _cf_err:
            logger.debug("[FullAuto] CausalFeedback 重建跳过: %s", _cf_err)

        # 健康检查结束：把完整 market_summary 写入 DB（供 UI「市场概览」展示）
        # [fix] 如果编排器评估超时，从 _market_scan_cache 回填编排器数据
        for sym in (session.symbols or []):
            if sym in market_summary and isinstance(market_summary[sym], dict):
                _ms_orch = market_summary[sym].get("orchestrator")
                if not _ms_orch or not isinstance(_ms_orch, dict) or not _ms_orch.get("long_bias"):
                    _cache_orch = (host.market_scan_cache.get(sym) or {}).get("orchestrator")
                    if _cache_orch and isinstance(_cache_orch, dict) and _cache_orch.get("long_bias"):
                        market_summary[sym]["orchestrator"] = _cache_orch
        host.ensure_market_prices(market_summary, list(session.symbols or []))
        for sym, info in (market_summary or {}).items():
            if isinstance(info, dict):
                host.normalize_orchestrator_for_ui(info)
                host.attach_scalp_advisory_for_ui(sym, info)

        session.last_market_summary = market_summary
        session.last_health_check_at = now
        for sym, info in (market_summary or {}).items():
            if isinstance(info, dict) and info.get("current_price"):
                host.market_scan_cache[sym] = dict(info)
        if market_summary:
            host.market_scan_cache_ts = time.time()
        host.safe_commit(db, "hc_market_summary_final", session=session)

        logger.info(
            f"[FullAuto] 健康检查完成 {session_id}: "
            f"active={len(active_ids)}, terminated={len(strategies_to_remove)}, "
            f"pnl={session.total_pnl or 0:.2f}, 耗时={time.time()-_hc_start:.1f}s"
        )
    except Exception as e:
        logger.error(f"[FullAuto] 健康检查失败 {session_id}: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        # Phase 0: 从追踪字典中移除并关闭 DB session
        host.active_db_sessions.pop(_db_track_key, None)
        db.close()
