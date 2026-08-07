"""快速编排器评估 — 从 monolith _run_quick_orchestrator_eval 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class QuickOrchHost:
    active_db_sessions: Dict[str, Any]
    deadlock_rescue_count: Dict[str, int]
    DEADLOCK_RESCUE_MAX: int = 3
    NATURE_TO_TIER_MAP: Dict[str, str] = field(default_factory=dict)

    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    active_exchange: Callable = field(repr=False, default=lambda: "binance")
    paper_loss_locks_disabled: Callable = field(repr=False, default=lambda *a, **k: False)
    get_lock_profile: Callable = field(repr=False, default=lambda *a, **k: type("P", (), {"ranging_pause": True})())
    record_strategy_pause: Callable = field(repr=False, default=lambda *a, **k: None)
    should_log_pause_event: Callable = field(repr=False, default=lambda *a, **k: True)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    clear_strategy_pause_meta: Callable = field(repr=False, default=lambda *a, **k: None)
    can_resume_strategy: Callable = field(repr=False, default=lambda *a, **k: True)
    safe_commit: Callable = field(repr=False, default=lambda *a, **k: True)


def build_quick_orch_host(svc) -> QuickOrchHost:
    return QuickOrchHost(
        active_db_sessions=svc._active_db_sessions,
        deadlock_rescue_count=svc._deadlock_rescue_count,
        DEADLOCK_RESCUE_MAX=svc._DEADLOCK_RESCUE_MAX,
        NATURE_TO_TIER_MAP=getattr(svc, "_NATURE_TO_TIER_MAP", {}) or {},
        get_trading_account_id=svc._get_trading_account_id,
        active_exchange=svc._active_exchange,
        paper_loss_locks_disabled=svc._paper_loss_locks_disabled,
        get_lock_profile=svc._get_lock_profile,
        record_strategy_pause=svc._record_strategy_pause,
        should_log_pause_event=svc._should_log_pause_event,
        append_event=svc._append_event,
        clear_strategy_pause_meta=svc._clear_strategy_pause_meta,
        can_resume_strategy=svc._can_resume_strategy,
        safe_commit=svc._safe_commit,
    )


def run_quick_orchestrator_eval(session_id: str, host: QuickOrchHost) -> None:
    from backend.database.connection import SessionLocal
    from backend.database.models import FullAutoSession, AIStrategy
    from backend.services.risk_control_service import RiskCheckResult

    db = SessionLocal()
    _db_track_key = f"{session_id}:quick_eval"
    host.active_db_sessions[_db_track_key] = db
    try:
        session = db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session or session.status != "running":
            return

        orchestrator_decisions = {}
        try:
            from backend.services.multi_timeframe_orchestrator import mt_orchestrator
            from backend.services.unified_data_pool import unified_data_pool
            snapshot = unified_data_pool.capture_snapshot(
                symbols=session.symbols,
                account_id=host.get_trading_account_id(db, session),
                environment=host.active_exchange(),
            )
            orchestrator_decisions = mt_orchestrator.evaluate_portfolio(
                session.symbols, snapshot
            )
        except Exception as e:
            logger.debug(f"[QuickEval] 编排器评估跳过: {e}")
            return

        if not orchestrator_decisions:
            return

        active_ids = list(session.active_strategy_ids or [])
        changed = False

        session_sids = set(session.active_strategy_ids or [])
        terminated_sids = set(session.terminated_strategy_ids or [])

        for symbol in session.symbols:
            orch_dec = orchestrator_decisions.get(symbol)
            if not orch_dec:
                continue

            recommended = orch_dec.recommended_slots or []

            # ── 快评暂停/恢复判定（按tier独立） ──
            # 核心原则：每个周期策略根据自己的信号独立决定暂停/恢复
            #   tier 对应编排器视图： short→short_view, mid→mid_view, long→long_view
            #   统一暂停条件：仅 frozen（风险事件）。三周期无信号不暂停（交给 LLM 判断）
            _is_frozen = orch_dec.final_action == "frozen"

            # 构建各 tier 的独立信号状态
            _tier_signal = {}  # {"short": {"bias": ..., "conf": ..., "has_signal": bool}}
            for _tv_key, _tv_obj in [("short", orch_dec.short_view), ("mid", orch_dec.mid_view), ("long", orch_dec.long_view)]:
                _tv_bias = getattr(_tv_obj, "bias", "neutral")
                _tv_conf = float(getattr(_tv_obj, "confidence", 0) or 0)
                _tv_has_signal = _tv_bias not in ("neutral",) and _tv_conf >= 0.20
                _tier_signal[_tv_key] = {"bias": _tv_bias, "conf": _tv_conf, "has_signal": _tv_has_signal}

            _signal_desc = "/".join(
                f"{k}={ts['bias']}({ts['conf']:.0%})" for k, ts in _tier_signal.items()
            )
            if getattr(orch_dec, "reasoning", ""):
                _signal_desc += f" | 综合={orch_dec.final_action}/{orch_dec.final_side or '-'}"
                _rs = str(orch_dec.reasoning or "")
                if len(_rs) > 120:
                    _rs = _rs[:120] + "…"
                _signal_desc += f" ({_rs})"

            matching = db.query(AIStrategy).filter(
                AIStrategy.primary_symbol == symbol,
                AIStrategy.status.in_(["active", "paused"]),
                AIStrategy.strategy_id.in_(session_sids),
                ~AIStrategy.strategy_id.in_(terminated_sids),
            ).all()

            # ── 判断是否需要三周期统一暂停 ──
            # 仅风险冻结（frozen）才触发统一暂停。
            # 三周期无信号不暂停 —— 交给 LLM 自行判断是否交易。
            # （与主 tick 循环 _unified_pause_sym = _is_frozen_sym 保持一致）
            _unified_pause = _is_frozen

            # ── P1-5/P1-7: 市场状态感知过滤 ──
            # 使用 MarketRegimeClassifier 检测震荡市(ranging)/崩盘(crash)
            # ranging 市场 WR=6.6% — 暂停 short/mid 开仓以保护本金
            _is_ranging = False
            _is_crash = False
            try:
                from backend.services.market_data import get_kline_data
                from backend.services.market_regime import MarketRegimeClassifier, MarketRegime as _MR
                import pandas as pd
                _klines = get_kline_data(
                    symbol=symbol, market="CRYPTO", period="1h", count=100,
                )
                if _klines is not None and len(_klines) >= 50:
                    _df = pd.DataFrame(_klines)
                    _classification = MarketRegimeClassifier().classify(_df)
                    _is_ranging = (_classification.regime == _MR.RANGING)
                    _is_crash = (_classification.regime == _MR.CRASH)
                    if _is_ranging or _is_crash:
                        logger.info(
                            f"[QuickEval] {symbol} 市场状态={_classification.regime.value} "
                            f"(conf={_classification.confidence:.0%}), "
                            f"{'暂停short/mid' if _is_ranging else '禁止开仓'}"
                        )
            except Exception as _regime_err:
                logger.debug(f"[QuickEval] 市场状态检测跳过 {symbol}: {_regime_err}")

            # S3: MetaStrategySelector — 市场状态自适应策略选择
            # 根据当前 regime 推荐策略权重，日志记录但不强制覆盖现有暂停逻辑
            try:
                from backend.services.meta_strategy_selector import meta_selector
                _market_ctx = {
                    "regime": _classification.regime.value if '_classification' in dir() else "unknown",
                    "regime_confidence": getattr(_classification, 'confidence', 0.5) if '_classification' in dir() else 0.5,
                    "volatility_ratio": 1.0,
                    "adx": 0,
                }
                # 构建策略池供 meta_selector 评估
                _pool = [
                    {"strategy_id": s.strategy_id, "symbol": s.primary_symbol,
                     "tier": getattr(s, 'timeframe_tier', 'mid'),
                     "trade_nature": (s.genome or {}).get("trade_nature", "swing")}
                    for s in matching
                ]
                _active_set = {s.strategy_id for s in matching if s.status == "active"}
                _meta_selection = meta_selector.select(db, _market_ctx, _pool, _active_set)
                if _meta_selection.paused_strategies:
                    logger.info(
                        f"[QuickEval|MetaSelector] {symbol} regime={_meta_selection.market_regime} "
                        f"建议暂停: {_meta_selection.paused_strategies} "
                        f"建议激活: {_meta_selection.activated_strategies}"
                    )
            except Exception as _ms_err:
                logger.debug(f"[QuickEval] MetaStrategySelector 跳过 {symbol}: {_ms_err}")

            # 震荡市：暂停 short/mid tier（不改 _unified_pause，per-strategy 判断）
            # 崩盘：全部暂停
            _effective_frozen = _unified_pause or _is_crash

            # 2026-05-08 深挖第 4 轮 修复：_consec_loss_pause 在循环内部更新但被使用前未初始化
            # 导致首次进入循环时 UnboundLocalError 直接打断整个 _run_quick_orchestrator_eval
            _consec_loss_pause = False

            # ── P5-fix(2026-05-08): 预先查询该 symbol 在每个 tier 是否有 open 持仓 ──
            # 已有持仓的 tier 不能因震荡市而暂停 — 否则 AI 不再为该持仓生成管理决策，
            # 持仓变成"无人看护"状态，只能等 SL/TP 被动触发。
            _tiers_with_open_pos: set = set()
            try:
                from backend.services.paper_trading_engine import paper_engine as _pe
                _trading_acct_id_qe = host.get_trading_account_id(db, session)
                for _p in (_pe.get_positions(db, _trading_acct_id_qe) or []):
                    if (_p.get("symbol") or "").upper() != symbol.upper():
                        continue
                    if (_p.get("status") or "open") != "open":
                        continue
                    _p_tier = (
                        (_p.get("timeframe_tier") or "").strip().lower()
                        or host.NATURE_TO_TIER_MAP.get(
                            (_p.get("trade_nature") or "").strip().lower(), "mid"
                        )
                    )
                    if _p_tier in ("short", "mid", "long"):
                        _tiers_with_open_pos.add(_p_tier)
            except Exception as _pos_check_err:
                logger.debug(f"[QuickEval] 持仓预检失败 {symbol}: {_pos_check_err}")

            for strat in matching:
                sid = strat.strategy_id
                # 确定该策略的 tier
                _strat_tier = (
                    getattr(strat, 'timeframe_tier', None)
                    or host.NATURE_TO_TIER_MAP.get(
                        (strat.genome or {}).get("trade_nature", ""), "mid")
                    if strat.genome else "mid"
                )
                _ts = _tier_signal.get(_strat_tier, _tier_signal.get("mid", {}))
                _this_tier_has_signal = _ts.get("has_signal", False)

                # ── P1-5: 震荡市暂停逻辑 ──
                # ranging 市场：暂停 short/mid tier（WR=6.6%），long tier 可持有
                # crash 市场：全部暂停（经 _effective_frozen 处理）
                # P5-fix(2026-05-08): 但若该 symbol+tier 已有持仓，必须保持 active，让 AI 持续管理
                _has_open_pos_this_tier = _strat_tier in _tiers_with_open_pos
                _orch_enter = str(getattr(orch_dec, "final_action", "") or "").lower() == "enter"
                _tier_orch_conf = float(
                    _tier_signal.get(_strat_tier, {}).get("conf", 0) or 0
                )
                # 震荡市：编排器明确 enter 且该 tier 置信度≥65% 时不暂停
                # P0-2: 从 0.35 提高到 0.65——历史数据显示震荡市 31.2% 胜率，低门槛导致大量亏损
                _orch_ranging_exempt = _orch_enter and _tier_orch_conf >= 0.65
                _ranging_pause = (
                    _is_ranging
                    and _strat_tier in ("short", "mid")
                    and not _has_open_pos_this_tier
                    and not _orch_ranging_exempt
                )

                # F1-2: 连续亏损暂停（独立于震荡市/崩盘） — 使用上一轮迭代或上次循环的 _consec_loss_pause
                if host.paper_loss_locks_disabled(session):
                    _should_pause = _is_crash
                elif not host.get_lock_profile(session).ranging_pause:
                    _should_pause = _is_crash or _consec_loss_pause or _unified_pause
                else:
                    _should_pause = _effective_frozen or _ranging_pause or _consec_loss_pause

                # P5-fix(2026-05-08): 已有持仓的 tier 即便被各种暂停策略命中，
                # 也要保持 active —— 否则 AI 不会为该持仓生成"管理决策"
                # （hold/reduce/close/adjust_sl）。
                # 持仓"无人看护"是这次诊断的核心问题之一。
                # 唯一例外：unified_pause（风险事件冻结）= 整个 symbol 都不能动，
                # 此时 AI 也不应再发任何信号，靠 paper_trading_engine 的硬止损保护。
                if _should_pause and _has_open_pos_this_tier and not _unified_pause:
                    _should_pause = False
                    # 标记为"持仓护持模式"，prompt 会降低 buy/sell 权重，鼓励 hold/reduce
                    # （tier_executor 通过 portfolio.balance._tier_pause_reason 传给 LLM）

                # 震荡市 long tier 不允许新开仓，但保持活跃（由 AI 决定是否减仓）
                _ranging_long_hold = _is_ranging and _strat_tier == "long" and not _effective_frozen

                # ── F1-2: 单策略连续亏损检测 ──
                _consec_loss_check = None
                if strat.status == "active" and not host.paper_loss_locks_disabled(session):
                    try:
                        from backend.services.risk_control_service import risk_control_service
                        _consec_loss_check = risk_control_service.check_strategy_consecutive_losses(
                            db, sid, symbol
                        )
                    except Exception as _consec_err:
                        logger.debug(f"[QuickEval] 连续亏损检查跳过 {sid}: {_consec_err}")

                _consec_loss_pause = (
                    _consec_loss_check is not None
                    and _consec_loss_check.result.value == RiskCheckResult.WARNING.value
                )

                if _should_pause:
                    if strat.status == "active":
                        strat.status = "paused"
                        if sid in active_ids:
                            active_ids.remove(sid)
                        if _consec_loss_pause:
                            _consec_count = (
                                _consec_loss_check.details.get("consecutive_losses", 3)
                                if _consec_loss_check else 3
                            )
                            _pause_reason = f"连续亏损{_consec_count}次暂停30分钟"
                        elif _is_frozen or _is_crash:
                            _pause_reason = "风险冻结" if _is_frozen else "崩盘禁止开仓"
                        elif _ranging_pause:
                            _pause_reason = f"震荡市暂停{_strat_tier}tier"
                        else:
                            _pause_reason = "未知原因暂停(fallback)"
                        host.record_strategy_pause(sid, _pause_reason, by="quick_eval")
                        # 2026-06-19: 统一注册到 SymbolLockRegistry
                        try:
                            from backend.services.symbol_lock_registry import lock_registry
                            _reason_code = "crash" if _is_frozen else (
                                "ranging" if _ranging_pause else "consec_loss"
                            )
                            lock_registry.lock(symbol, strategy_id=sid,
                                               reason_code=_reason_code, by="quick_eval")
                        except Exception:
                            pass
                        if host.should_log_pause_event(
                            session.session_id, f"pause:{sid}:{_pause_reason[:12]}"
                        ):
                            host.append_event(session, "quick_eval_pause",
                                f"快速评估: {symbol}/{_strat_tier} {_pause_reason} → 暂停 | {_signal_desc}")
                        logger.info(f"[QuickEval] {symbol}/{_strat_tier} {_pause_reason}，策略暂停")
                        changed = True
                elif strat.status == "paused":
                    # ── 非暂停状态：恢复被暂停的策略（震荡市需满最短暂停时长）──
                    if host.paper_loss_locks_disabled(session):
                        strat.status = "active"
                        if sid not in active_ids:
                            active_ids.append(sid)
                        host.clear_strategy_pause_meta(sid)
                        changed = True
                        continue
                    if not host.can_resume_strategy(sid, is_ranging_pause=_ranging_pause):
                        continue
                    strat.status = "active"
                    if sid not in active_ids:
                        active_ids.append(sid)
                    host.clear_strategy_pause_meta(sid)
                    _resume_detail = "ranging-long持仓管理" if _ranging_long_hold else "非统一暂停"
                    if host.should_log_pause_event(session.session_id, f"resume:{sid}"):
                        host.append_event(session, "quick_eval_resume",
                            f"快速评估: {symbol}/{_strat_tier} 恢复活跃 ({_resume_detail})")
                    logger.info(f"[QuickEval] {symbol}/{_strat_tier} 信号恢复，策略重启")
                    changed = True
                # active + 非暂停状态：保持活跃

            # ── 死锁安全阀：如果该symbol所有策略都被暂停且无风险事件，强制恢复 ──
            # 震荡市下 short/mid 全部暂停属于正常行为，不算死锁
            if matching and not _effective_frozen and not _is_ranging:
                _all_paused = all(s.status == "paused" for s in matching)
                if _all_paused:
                    # D7修复: 死锁熔断 — 同品种3次死锁恢复后直接熔断
                    _deadlock_count = host.deadlock_rescue_count.get(symbol, 0) + 1
                    host.deadlock_rescue_count[symbol] = _deadlock_count
                    if _deadlock_count >= host.DEADLOCK_RESCUE_MAX:
                        logger.error(
                            f"[QuickEval] {symbol} 死锁熔断触发: "
                            f"已连续{_deadlock_count}次死锁恢复，强制熔断该品种所有策略"
                        )
                        for _strat in matching:
                            if _strat.status in ("paused", "active"):
                                _strat.status = "frozen"
                                _sid = _strat.strategy_id
                                if _sid in active_ids:
                                    active_ids.remove(_sid)
                                # 2026-06-19: 死锁注册到 registry（24h TTL，不再永久）
                                from backend.services.symbol_lock_registry import lock_registry
                                lock_registry.lock(symbol, strategy_id=str(_sid),
                                                   reason_code="deadlock", by="quick_eval")
                                host.append_event(session, "deadlock_fuse",
                                    f"死锁熔断: {symbol}/{getattr(_strat, 'timeframe_tier', 'mid')} "
                                    f"连续{_deadlock_count}次死锁，强制冻结")
                        # 重置计数器（熔断后）
                        host.deadlock_rescue_count[symbol] = 0
                        changed = True
                    else:
                        logger.warning(
                            f"[QuickEval] {symbol} 死锁检测({_deadlock_count}/{host.DEADLOCK_RESCUE_MAX}): "
                            f"所有策略暂停但无风险事件，强制恢复"
                        )
                        for _strat in matching:
                            if _strat.status == "paused":
                                _strat.status = "active"
                                _sid = _strat.strategy_id
                                if _sid not in active_ids:
                                    active_ids.append(_sid)
                                host.clear_strategy_pause_meta(_sid)
                                _rescue_tier = (
                                    getattr(_strat, 'timeframe_tier', None)
                                    or host.NATURE_TO_TIER_MAP.get(
                                        (_strat.genome or {}).get("trade_nature", ""), "mid")
                                    if _strat.genome else "mid"
                                )
                                if host.should_log_pause_event(
                                    session.session_id, f"deadlock:{symbol}"
                                ):
                                    host.append_event(session, "quick_eval_deadlock_rescue",
                                        f"死锁救援({_deadlock_count}/{host.DEADLOCK_RESCUE_MAX}): "
                                        f"{symbol}/{_rescue_tier} 强制恢复活跃")
                        changed = True
                elif symbol in host.deadlock_rescue_count:
                    # 死锁解除：重置该品种计数器
                    _prev = host.deadlock_rescue_count.pop(symbol, 0)
                    if _prev > 0:
                        logger.info(f"[QuickEval] {symbol} 死锁已解除，重置计数器 (was {_prev})")

        # ── 快评 tick 不再独立调用 AI 决策（统一走 _run_analyst_system 路径）──
        # 快评仅负责策略暂停/恢复，所有交易决策由完整 tick 的 master 路径统一处理
        # if session.status == "running" and active_ids:
        #     host._execute_ai_decisions(db, session, active_ids, orchestrator_decisions)

        if changed:
            session.active_strategy_ids = active_ids
            host.safe_commit(db, "quick_eval", session=session)
    except Exception as e:
        db.rollback()
        raise
    finally:
        host.active_db_sessions.pop(_db_track_key, None)
        db.close()
