"""分析师系统 — 从 monolith _run_analyst_system* 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class AnalystSystemHost:
    market_scan_cache: Dict[str, Any]
    long_tier_staged_tp_state: Dict[str, Any]
    tick_symbol_subset: Dict[str, Set[str]]
    pre_screen_results: Any = None
    pre_screen_passed: Set[str] = field(default_factory=set)
    mlto_handled_keys: Set[str] = field(default_factory=set)

    clear_master_strat_cache: Callable = field(repr=False, default=lambda: None)
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    sync_hold_timeout_alerts: Callable = field(repr=False, default=lambda *a, **k: None)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    annotate_auto_coin_meta: Callable = field(repr=False, default=lambda *a, **k: None)
    build_fast_stability_result: Callable = field(repr=False, default=lambda *a, **k: {})
    run_with_timeout: Callable = field(repr=False, default=lambda *a, **k: None)
    record_ai_failure: Callable = field(repr=False, default=lambda *a, **k: None)
    record_ai_success: Callable = field(repr=False, default=lambda *a, **k: None)
    validate_ai_decisions: Callable = field(repr=False, default=lambda *a, **k: {})
    inject_orch_scheduled_stubs: Callable = field(repr=False, default=lambda *a, **k: [])
    execute_master_decisions: Callable = field(repr=False, default=lambda *a, **k: None)
    maintain_mlto_theses_for_session: Callable = field(repr=False, default=lambda *a, **k: None)
    execute_ai_decisions: Callable = field(repr=False, default=lambda *a, **k: None)
    execute_defensive_analysis: Callable = field(repr=False, default=lambda *a, **k: None)


def build_analyst_system_host(svc) -> AnalystSystemHost:
    # MidLong v2 C7：与 midlong 独立循环共享同一 set，禁止拷贝后回写空集冲掉占位
    handled = getattr(svc, "_mlto_handled_keys", None)
    if not isinstance(handled, set):
        handled = set(handled or [])
        svc._mlto_handled_keys = handled
    return AnalystSystemHost(
        market_scan_cache=svc._market_scan_cache,
        long_tier_staged_tp_state=svc._long_tier_staged_tp_state,
        tick_symbol_subset=svc._tick_symbol_subset,
        pre_screen_results=getattr(svc, "_pre_screen_results", None),
        pre_screen_passed=set(getattr(svc, "_pre_screen_passed", None) or []),
        mlto_handled_keys=handled,
        clear_master_strat_cache=svc._clear_master_strat_cache,
        get_trading_account_id=svc._get_trading_account_id,
        sync_hold_timeout_alerts=svc._sync_hold_timeout_alerts,
        append_event=svc._append_event,
        annotate_auto_coin_meta=svc._annotate_auto_coin_meta,
        build_fast_stability_result=svc._build_fast_stability_result,
        run_with_timeout=svc._run_with_timeout,
        record_ai_failure=svc._record_ai_failure,
        record_ai_success=svc._record_ai_success,
        validate_ai_decisions=svc._validate_ai_decisions,
        inject_orch_scheduled_stubs=svc._inject_orch_scheduled_stubs,
        execute_master_decisions=svc._execute_master_decisions,
        maintain_mlto_theses_for_session=svc._maintain_mlto_theses_for_session,
        execute_ai_decisions=svc._execute_ai_decisions,
        execute_defensive_analysis=svc._execute_defensive_analysis,
    )


def run_analyst_system(
    db: Session,
    session,
    active_ids: list,
    market_summary: dict,
    host: AnalystSystemHost,
) -> None:
    from backend.database.models import Account, AIStrategy as _AIStrategy
    from backend.config.settings import FULLAUTO_AI_UNIFIED_ANALYSIS

    # 防御性 rollback：数据采集阶段可能查询不存在的表导致 session 事务污染
    # [fix] rollback 后 session ORM 对象会被 detach，需要 merge 回来
    try:
        db.rollback()
        # rollback 后重新将 session 对象绑定到 db（避免 "not persistent" 错误）
        session = db.merge(session)
    except Exception:
        pass
    # rollback / 上一 tick 的 db.close 会使 _master_strat_cache 中的 AIStrategy detach
    host.clear_master_strat_cache()

    mode = session.status
    account = db.query(Account).filter(Account.id == session.account_id).first()
    if not account:
        return

    if FULLAUTO_AI_UNIFIED_ANALYSIS:
        run_analyst_system_unified(
            db, session, account, active_ids, market_summary, host,
        )
        return

    # [已清理] legacy 三 tier 并行路径已移除（FULLAUTO_AI_UNIFIED_ANALYSIS 默认 true）
    # 统一分析路径在上方的 return 处结束
    logger.warning("[FullAuto] FULLAUTO_AI_UNIFIED_ANALYSIS=false 已不再支持，强制走统一分析")
    run_analyst_system_unified(
        db, session, account, active_ids, market_summary, host,
    )


def run_analyst_system_unified(
    db: Session,
    session,
    account,
    active_ids: list,
    market_summary: dict,
    host: AnalystSystemHost,
) -> None:
    from backend.database.models import AIStrategy as _AIStrategy, Account as _AcctModel
    from backend.services.trading_analysts import analyst_system, merge_reports_with_tier_slices

    mode = session.status

    try:
        from backend.services.paper_trading_engine import paper_engine

        # P5-fix(2026-05-08): paper模式资金池/持仓必须用 paper_account_id
        _trading_acct_id = host.get_trading_account_id(db, session)
        _eff_acct = (
            db.query(_AcctModel).filter(_AcctModel.id == _trading_acct_id).first()
            if _trading_acct_id != getattr(account, "id", None) else account
        )
        positions_list = paper_engine.get_positions(db, _eff_acct.id) or []
        bal_info = paper_engine.get_balance(db, _eff_acct.id) or {}
        host.sync_hold_timeout_alerts(_trading_acct_id, positions_list, bal_info)

        from backend.services.strategy_analysis_context import (
            build_strategy_meta_cache,
            enrich_positions_with_strategy_meta,
        )
        pos_strat_ids = [p.get("strategy_id") for p in positions_list if p.get("strategy_id")]
        _strat_meta_cache = build_strategy_meta_cache(db, pos_strat_ids)
        enrich_positions_with_strategy_meta(positions_list, _strat_meta_cache)

        # ── 孤立持仓检测：持仓中不在 session.symbols 的币种 ──
        _session_sym_upper = {s.upper() for s in (session.symbols or [])}
        _orphan_syms: set = set()
        for _p in positions_list:
            _psym = (_p.get("symbol") or "").upper()
            if _psym and _psym not in _session_sym_upper:
                _orphan_syms.add(_psym)
        if _orphan_syms:
            for _osym in _orphan_syms:
                if _osym not in (market_summary or {}):
                    _cached = host.market_scan_cache.get(_osym)
                    if _cached and isinstance(_cached, dict) and _cached.get("current_price"):
                        if market_summary is None:
                            market_summary = {}
                        market_summary[_osym] = dict(_cached)
            logger.info(
                f"[FullAuto] 孤立持仓检测: {len(_orphan_syms)} 个币种 "
                f"({', '.join(sorted(_orphan_syms))}) 不在会话交易对中，已补充行情"
            )
        _effective_symbols = list(set(session.symbols or []) | _orphan_syms)
        from backend.config.settings import FULLAUTO_AI_DOMINANT, MIDLONG_AI_MANDATORY
        _tick_subset = None if (FULLAUTO_AI_DOMINANT or MIDLONG_AI_MANDATORY) else host.tick_symbol_subset.get(
            getattr(session, "session_id", "")
        )
        if _tick_subset:
            _effective_symbols = [
                s for s in _effective_symbols
                if str(s).upper() in _tick_subset
            ]
            positions_list = [
                p for p in positions_list
                if str(p.get("symbol") or "").upper() in _tick_subset
            ]
            logger.info(
                f"[FullAuto] 本轮统一分析限流: symbols={sorted(_tick_subset)}, "
                f"positions={len(positions_list)}"
            )

        # ══════════════════════════════════════════════════════
        # P2 D14: long tier 分批战略 TP 扫描（独立于 AI 决策）
        # 在所有 AI 分析之前执行，close_reason=tp_staged_N/trailing_hit
        # 会被 D13 判定为硬退出，不被 long-immune 屏蔽。
        # ══════════════════════════════════════════════════════
        try:
            from backend.config.settings import RISK_USE_LONG_TIER_STAGED_TP, RISK_USE_NATURE_EXIT_ORCHESTRATOR
            from backend.services.risk_band_resolver import stage_e_active
            if stage_e_active() and RISK_USE_NATURE_EXIT_ORCHESTRATOR and positions_list:
                from backend.services.position_exit_orchestrator import position_exit_orchestrator
                _peo_changes = position_exit_orchestrator.evaluate_and_execute(
                    db=db,
                    account_id=_eff_acct.id,
                    positions=positions_list,
                    market_summary=market_summary or {},
                    session=session,
                    append_event=host.append_event,
                )
                if _peo_changes > 0:
                    positions_list = paper_engine.get_positions(db, _eff_acct.id) or []
                    enrich_positions_with_strategy_meta(positions_list, _strat_meta_cache)
                    logger.info(
                        f"[FullAuto][PEO] 本 tick 触发 {_peo_changes} 次 nature exit 动作，"
                        f"重新拉取持仓（{len(positions_list)} 个）"
                    )
            elif stage_e_active() and RISK_USE_LONG_TIER_STAGED_TP and positions_list:
                # [DEPRECATED Phase E] long_tier_staged_tp 是 PEO (position_exit_orchestrator)
                # 接管前的 pre-Stage-E 实现, 仅在 RISK_USE_NATURE_EXIT_ORCHESTRATOR=false
                # 时通过 elif 命中。默认配置 (PEO on) 走上一分支, 本分支为兜底/灰度回退路径。
                # 验证完毕后由后续清理 PR 与 long_tier_staged_tp.py 一并删除, 新代码请勿扩展。
                from backend.services.long_tier_staged_tp import (
                    check as _staged_tp_check,
                    StagedTpState as _StagedTpState,
                )
                _staged_changes = 0
                _active_pids = set()
                for _lp in positions_list:
                    _lp_tier = (_lp.get("timeframe_tier") or "mid").strip().lower()
                    if _lp_tier != "long":
                        continue
                    _pid = _lp.get("id")
                    if not _pid:
                        continue
                    _active_pids.add(_pid)
                    _sym_lp = _lp.get("symbol", "")
                    _side_lp = _lp.get("side", "")
                    _entry_lp = float(_lp.get("entry_price", 0) or 0)
                    _mark_lp = float(_lp.get("mark_price", 0) or _entry_lp)
                    if _entry_lp <= 0 or _mark_lp <= 0 or not _sym_lp or not _side_lp:
                        continue
                    _mkt_lp = (market_summary or {}).get(_sym_lp, {}) if isinstance(market_summary, dict) else {}
                    _atr_pct_lp = 0.02
                    if isinstance(_mkt_lp, dict):
                        _atr_pct_lp = float(_mkt_lp.get("volatility_value", 0.02) or 0.02)
                    _state_key = f"pos_{_pid}"
                    _state_lp = host.long_tier_staged_tp_state.get(_state_key)
                    if _state_lp is None:
                        _state_lp = _StagedTpState()
                        host.long_tier_staged_tp_state[_state_key] = _state_lp
                    _decision = _staged_tp_check(
                        entry_price=_entry_lp, current_price=_mark_lp,
                        side=_side_lp, atr_pct=_atr_pct_lp, state=_state_lp,
                    )

                    if _decision.action == "reduce":
                        _pos_size = float(_lp.get("size", 0) or 0)
                        _qty = round(_pos_size * _decision.reduce_ratio, 8)
                        if _qty > 0:
                            _reason = f"tp_staged_{(_decision.stage_idx or 0) + 1}"
                            try:
                                _res = paper_engine.close_position(
                                    db, _eff_acct.id, _sym_lp, _side_lp,
                                    reason=_reason, quantity=_qty,
                                    strategy_id=_lp.get("strategy_id"),
                                )
                                if _res:
                                    _staged_changes += 1
                                    _pnl_lp = _res.get("pnl", 0)
                                    session.total_trades = (session.total_trades or 0) + 1
                                    if _pnl_lp > 0:
                                        session.winning_trades = (session.winning_trades or 0) + 1
                                    host.append_event(session, "long_staged_tp",
                                        f"🎯 {_sym_lp}[long] 分批TP{(_decision.stage_idx or 0) + 1}: "
                                        f"减仓{_decision.reduce_ratio:.0%} PnL=${_pnl_lp:+.2f}")
                                    logger.info(
                                        f"[FullAuto][P2.D14] {_sym_lp} long staged_tp "
                                        f"stage={(_decision.stage_idx or 0) + 1} "
                                        f"ratio={_decision.reduce_ratio:.0%} pnl=${_pnl_lp:+.2f}"
                                    )
                            except Exception as _red_err:
                                logger.warning(f"[FullAuto][P2.D14] {_sym_lp} staged reduce 失败: {_red_err}")

                    elif _decision.action == "trailing_hit":
                        try:
                            _res = paper_engine.close_position(
                                db, _eff_acct.id, _sym_lp, _side_lp,
                                reason="trailing_hit",
                                strategy_id=_lp.get("strategy_id"),
                            )
                            if _res:
                                _staged_changes += 1
                                host.long_tier_staged_tp_state.pop(_state_key, None)
                                _pnl_lp = _res.get("pnl", 0)
                                session.total_trades = (session.total_trades or 0) + 1
                                if _pnl_lp > 0:
                                    session.winning_trades = (session.winning_trades or 0) + 1
                                host.append_event(session, "long_trailing_hit",
                                    f"🎯 {_sym_lp}[long] trailing hit 全平 PnL=${_pnl_lp:+.2f}")
                                logger.info(
                                    f"[FullAuto][P2.D14] {_sym_lp} long trailing_hit pnl=${_pnl_lp:+.2f}"
                                )
                        except Exception as _th_err:
                            logger.warning(f"[FullAuto][P2.D14] {_sym_lp} trailing_hit 失败: {_th_err}")

                    elif _decision.action == "trailing_update" and _decision.suggested_sl_price:
                        try:
                            paper_engine.update_position_tp_sl(
                                db, _pid, sl_price=_decision.suggested_sl_price,
                            )
                            logger.debug(
                                f"[FullAuto][P2.D14] {_sym_lp} trailing SL "
                                f"→ ${_decision.suggested_sl_price}"
                            )
                        except Exception as _upd_err:
                            logger.debug(f"[FullAuto][P2.D14] SL 更新失败: {_upd_err}")

                # GC: 清理已平仓 position 的 state（避免内存泄漏）
                _stale_keys = [
                    k for k in list(host.long_tier_staged_tp_state.keys())
                    if k.startswith("pos_") and int(k[4:]) not in _active_pids
                ]
                for _k in _stale_keys:
                    host.long_tier_staged_tp_state.pop(_k, None)
                if _stale_keys:
                    logger.debug(f"[FullAuto][P2.D14] 清理 {len(_stale_keys)} 个已平仓 state")

                # 如果有实际变动，重新拉一次 positions_list（后续逻辑都要基于最新持仓）
                if _staged_changes > 0:
                    positions_list = paper_engine.get_positions(db, _eff_acct.id) or []
                    enrich_positions_with_strategy_meta(positions_list, _strat_meta_cache)
                    logger.info(
                        f"[FullAuto][P2.D14] 本 tick 触发 {_staged_changes} 次 long staged 动作，"
                        f"重新拉取持仓（{len(positions_list)} 个）"
                    )
        except Exception as _stg_err:
            logger.warning(f"[FullAuto][P2.D14] long staged TP 扫描异常(不影响主流程): {_stg_err}")

        intel_data = {}
        for sym, info in (market_summary or {}).items():
            if isinstance(info, dict):
                intel_data[sym] = {
                    "sentiment_index": info.get("sentiment_index", 50),
                    "sentiment_zone": info.get("sentiment_zone", "neutral"),
                    "whale_direction": info.get("whale_direction", 0),
                    "derivatives_signal": info.get("derivatives_signal", "neutral"),
                    "funding_rate": info.get("funding_rate", 0),
                    "news_top_event": info.get("news_top_event", ""),
                    "news_impact": info.get("news_impact", 0),
                }

        _total_trades = session.total_trades or 0
        _winning_trades = session.winning_trades or 0
        _real_win_rate = round(_winning_trades / _total_trades, 3) if _total_trades > 0 else 0
        session_stats = {
            "current_drawdown": getattr(session, "current_drawdown", 0) or 0,
            "max_drawdown": session.max_drawdown or 0,
            "max_total_drawdown_pct": session.max_total_drawdown_pct or 0.30,
            "total_pnl": session.total_pnl or 0,
            "win_rate": _real_win_rate,
            "total_trades": _total_trades,
            "winning_trades": _winning_trades,
        }

        from backend.services.strategy_analysis_context import build_strategies_for_analysis
        strategies_info = build_strategies_for_analysis(db, list(active_ids or []))

    except Exception as e:
        logger.warning(f"[FullAuto] 分析师数据收集失败: {e}")
        return

    # ── 混合信号模式：技术指标预筛选（零LLM调用） ──
    _pre_screen_results = None
    _pre_screen_passed = set()
    _pre_screen_section = ""
    try:
        from backend.config.settings import (
            HYBRID_SIGNAL_MODE_ENABLED, PRESCREENER_ENABLED,
        )
        if HYBRID_SIGNAL_MODE_ENABLED and PRESCREENER_ENABLED:
            from backend.services.signal_pre_screener import get_signal_pre_screener
            from backend.services.signal_frequency_guard import get_signal_frequency_guard

            _screener = get_signal_pre_screener()
            _freq_guard = get_signal_frequency_guard()

            # 对所有有效标的做预筛选
            _batch = _screener.screen_batch(
                _effective_symbols, market_summary or {}, tier="short",
            )

            # 频率保障：如果当日信号不足，注入最有潜力的标的
            _guaranteed = _freq_guard.get_guaranteed_symbols(
                "short", _effective_symbols, market_summary or {},
            )
            _batch.guaranteed_symbols = _guaranteed

            _pre_screen_results = _batch
            _pre_screen_passed = set(_batch.passed_symbols + _guaranteed)

            # 生成注入 LLM 的预筛选段落
            _pre_screen_section = _screener.format_prescreen_prompt_section(
                _batch, tier="short",
            )

            if _pre_screen_passed:
                logger.info(
                    f"[FullAuto][混合模式] 预筛选通过 {_len_ := len(_pre_screen_passed)}/{len(_effective_symbols)} "
                    f"+ 频率保障 {len(_guaranteed)}"
                )
            else:
                logger.debug(
                    f"[FullAuto][混合模式] 预筛选通过 0/{len(_effective_symbols)}"
                )
    except Exception as _ps_err:
        logger.debug(f"[FullAuto][混合模式] 预筛选跳过(非致命): {_ps_err}")

    try:
        from backend.config.settings import (
            FULLAUTO_FAST_DECISION_MODE,
            compute_qaa_analyst_timeout,
            QAA_ANALYST_STREAM_SAFETY_CAP_S,
        )
        from backend.services.llm_config_service import (
            get_llm_config_for_analysis,
            should_use_llm_streaming,
        )

        _acct_id = getattr(session, "account_id", None) or getattr(account, "id", None)

        # [2026-08-07 修复] 长事务拆分：LLM 分析（流式 10-100s+）前先提交主连接事务，
        # 避免分析期间连接 idle-in-transaction（>20s 被 LeakGuard 告警、>120s 被强制
        # 终止，导致本 tick 决策落库失败 + 持仓 UPDATE 被行锁阻塞）。
        try:
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass

        # AI 精选币打标：让 Master LLM 看到选币评分并差异化加权
        host.annotate_auto_coin_meta(
            getattr(session, "session_id", "") or "", market_summary or {})

        def _do_full_analysis():
            return analyst_system.run_full_analysis(
                positions=positions_list,
                market_envs=market_summary or {},
                intel_data=intel_data,
                balance=bal_info,
                session_stats=session_stats,
                strategies=strategies_info,
                symbols=_effective_symbols,
                mode=mode,
                db=db,
                account_id=_acct_id,
            )

        _timeout_used: Optional[float] = None
        if FULLAUTO_FAST_DECISION_MODE:
            result = host.build_fast_stability_result(
                _effective_symbols, trigger="forced",
            )
            logger.info(
                f"[FullAuto] 强制快速稳定模式: symbols={len(_effective_symbols)}"
            )
        else:
            try:
                _llm_cfg = get_llm_config_for_analysis(_acct_id)
                _use_stream = should_use_llm_streaming(_llm_cfg)
                if _use_stream:
                    _cap = QAA_ANALYST_STREAM_SAFETY_CAP_S
                    logger.info(
                        f"[FullAuto] 深度分析(流式): symbols={len(_effective_symbols)}"
                        f"{f', 防挂死上限={_cap:.0f}s' if _cap > 0 else '（无外层窗口）'}"
                    )
                    if _cap > 0:
                        _timeout_used = _cap
                        result = host.run_with_timeout(
                            _do_full_analysis,
                            timeout_s=_cap,
                            fallback=None,
                            label="unified_run_full_analysis_stream",
                        )
                    else:
                        result = _do_full_analysis()
                else:
                    _timeout_used = compute_qaa_analyst_timeout(
                        symbol_count=len(_effective_symbols),
                        account_id=_acct_id,
                    )
                    logger.info(
                        f"[FullAuto] 深度分析(非流式): symbols={len(_effective_symbols)}, "
                        f"timeout={_timeout_used:.0f}s"
                    )
                    result = host.run_with_timeout(
                        _do_full_analysis,
                        timeout_s=_timeout_used,
                        fallback=None,
                        label="unified_run_full_analysis",
                    )
            except Exception as _analysis_err:
                logger.error(
                    f"[FullAuto] 深度分析异常: {_analysis_err}", exc_info=True,
                )
                result = None

            if not result:
                result = host.build_fast_stability_result(
                    _effective_symbols,
                    trigger="timeout",
                    timeout_s=_timeout_used,
                )
                logger.warning(
                    f"[FullAuto] 深度分析超时/失败，降级保守 hold: "
                    f"symbols={len(_effective_symbols)}, timeout={_timeout_used}"
                )
                host.record_ai_failure(session, "深度分析超时或失败，降级 hold")
            else:
                logger.info(
                    f"[FullAuto] 深度分析完成: symbols={len(_effective_symbols)}"
                )

        # [fix] run_full_analysis 内部通过 db 参数查询 StrategyMemory、
        # experience_retriever 等，如果任何查询失败，PostgreSQL session 进入
        # InFailedSqlTransaction 状态。此处主动 rollback 清除可能的污染，
        # 确保后续 _execute_master_decisions 拿到干净的 session。
        try:
            db.rollback()
            session = db.merge(session)
        except Exception:
            pass
        host.clear_master_strat_cache()

        session.analyst_reports = merge_reports_with_tier_slices(result)

        # 存储预筛选结果供 _execute_master_decisions 使用
        host.pre_screen_results = _pre_screen_results
        host.pre_screen_passed = _pre_screen_passed

        master = result.get("master_decision", {})
        session.master_decision = master
        overall = master.get("overall_assessment", "")
        risk_level = master.get("risk_level", "medium")
        decisions = master.get("decisions", [])

        is_rule_fallback = (not overall and risk_level == "medium"
                            and all(d.get("reasoning", "").startswith("[规则")
                                    for d in decisions))
        from backend.services.data_readiness_gate import (
            is_rule_fallback_decision,
            strip_rule_fallback_opens,
        )
        if is_rule_fallback_decision(master):
            is_rule_fallback = True
        _analysis_degraded = bool(result.get("_analysis_degraded"))
        if is_rule_fallback:
            host.record_ai_failure(session, "LLM 不可用，已降级到规则引擎")
            master = strip_rule_fallback_opens(master)
            decisions = master.get("decisions", [])
        elif _analysis_degraded:
            pass  # 超时/强制快速模式已在上方记录失败或跳过成功计数
        else:
            host.record_ai_success(session)
            # 刷新 session ORM 对象，获取自动选币最新注入的 symbols
            # 避免自动选币在另一个 DB session 中添加了新币种但此处仍用旧值
            try:
                db.refresh(session)
            except Exception:
                # session 对象可能已从 db 中 detach，重新查询
                try:
                    from backend.database.models import FullAutoSession as _FAS
                    session = db.query(_FAS).filter(_FAS.session_id == session.session_id).first() or session
                except Exception:
                    pass
            # 使用 _effective_symbols（含孤立持仓）而非 session.symbols
            # 避免孤立持仓的决策被审核拒绝
            _audit_symbols = list(set(session.symbols or []) | _orphan_syms)
            master = host.validate_ai_decisions(
                session, master, _audit_symbols, positions_list)
            overall = master.get("overall_assessment", "")
            risk_level = master.get("risk_level", "medium")
            decisions = master.get("decisions", [])

        host.append_event(session, "analyst_synthesis",
            f"📊 总控决策(统一模式): {overall} | 风险={risk_level}")

        # Fix 18: 总控独立调度中长线 agent（stub-only → Execute 单次 LLM）
        try:
            decisions = host.inject_orch_scheduled_stubs(
                decisions, market_summary or {}, session=session,
            )
        except Exception as _orch_route_err:
            logger.debug(f"[Fix18] 独立调度异常(非致命): {_orch_route_err}")

        # MidLong v2 C7：禁止每轮清空 mlto_handled_keys（会冲掉独立循环占位）

        logger.info(f"[FullAuto] 统一分析完成: mode={mode}, "
                    f"risk={risk_level}, decisions={len(decisions)}")

        if decisions:
            host.execute_master_decisions(
                db, session, _trading_acct_id, decisions, positions_list,
                active_ids, market_summary, mode,
                analyst_reports=merge_reports_with_tier_slices(result),
                balance_info=bal_info)

        try:
            from backend.config.settings import MIDLONG_AGENT_INDEPENDENT_SCHEDULER
            _ml_independent = bool(MIDLONG_AGENT_INDEPENDENT_SCHEDULER)
            host.maintain_mlto_theses_for_session(
                session=session,
                market_summary=market_summary or {},
                analyst_reports=merge_reports_with_tier_slices(result),
                mode=mode,
                portfolio={"balance": bal_info, "positions": positions_list},
                run_mid=not _ml_independent,
                run_long=not _ml_independent,
            )
        except Exception as _mlto_maint:
            logger.debug("[MLTO] maintain after unified: %s", _mlto_maint)

    except Exception as e:
        logger.error(f"[FullAuto] 分析师系统异常: {e}", exc_info=True)
        try:
            db.rollback()
            session = db.merge(session)
        except Exception:
            pass
        host.clear_master_strat_cache()
        host.record_ai_failure(session, str(e))
        host.append_event(session, "analyst_error",
            f"分析系统异常: {str(e)[:80]}", severity="critical")
        from backend.config.settings import FULLAUTO_ANALYST_FALLBACK
        if FULLAUTO_ANALYST_FALLBACK == "legacy":
            # [2026-06-21] legacy 回退也必须过 V5 门控
            _legacy_skip = False
            try:
                from backend.services.decision_core.unified_gate import evaluate_entry
                for _ld in (decisions or []):
                    _la = (_ld.get("action") or "").lower()
                    if _la in ("buy", "sell"):
                        _lg = evaluate_entry(
                            db=db, account_id=_trading_acct_id,
                            symbol=_ld.get("symbol", "?"),
                            action=_la,
                            confidence=float(_ld.get("confidence", 40)),
                            tier=_ld.get("timeframe_tier", "mid"),
                            trade_nature=_ld.get("trade_nature", "swing"),
                            tp_pct=None, sl_pct=None, mode=mode,
                        )
                        if not _lg.allowed:
                            _legacy_skip = True
                            host.append_event(session, "legacy_gate_block",
                                f"Legacy回退被V5拦截 {_ld.get('symbol','?')}: {_lg.reason}")
            except Exception as _legacy_gate_err:
                # fail-closed：门控检查异常与门控拒绝同等对待，legacy 回退路径
                # 本身就是"分析师系统已经异常"之后的兜底，不能再叠加"门控也不生效"。
                _legacy_skip = True
                logger.warning(
                    f"[FullAuto] Legacy回退门控检查异常: {_legacy_gate_err}，按 fail-closed 跳过本轮回退开仓"
                )
            if not _legacy_skip:
                if mode == "running" and active_ids:
                    host.execute_ai_decisions(db, session, active_ids, market_summary)
                elif mode == "defensive":
                    host.execute_defensive_analysis(db, session, market_summary)
            host.append_event(session, "analyst_fallback",
                "分析师异常回退到旧执行路径", severity="warning")
        else:
            host.append_event(session, "analyst_error_noop",
                f"分析师异常（不回退）: {str(e)[:60]}", severity="warning")
