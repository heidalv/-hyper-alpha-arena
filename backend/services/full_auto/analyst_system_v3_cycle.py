"""QAA v3 分析师路径 — 从 monolith _run_analyst_system_v3 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class AnalystV3Host:
    active_db_sessions: Dict[str, Any]
    mlto_handled_keys: Set[str] = field(default_factory=set)
    annotate_auto_coin_meta: Callable = field(repr=False, default=lambda *a, **k: None)
    build_fast_stability_result: Callable = field(repr=False, default=lambda *a, **k: {})
    run_with_timeout: Callable = field(repr=False, default=lambda *a, **k: None)
    inject_orch_scheduled_stubs: Callable = field(repr=False, default=lambda *a, **k: [])
    execute_master_decisions: Callable = field(repr=False, default=lambda *a, **k: None)
    maintain_mlto_theses_for_session: Callable = field(repr=False, default=lambda *a, **k: None)
    write_qaa_v3_forced_decision_logs: Callable = field(repr=False, default=lambda *a, **k: None)


def build_analyst_v3_host(svc) -> AnalystV3Host:
    from backend.services.full_auto.qaa_v3_forced_logs import write_qaa_v3_forced_decision_logs
    # MidLong v2 C7：共享同一 set，禁止拷贝后回写空集
    handled = getattr(svc, "_mlto_handled_keys", None)
    if not isinstance(handled, set):
        handled = set(handled or [])
        svc._mlto_handled_keys = handled
    return AnalystV3Host(
        active_db_sessions=svc._active_db_sessions,
        mlto_handled_keys=handled,
        annotate_auto_coin_meta=svc._annotate_auto_coin_meta,
        build_fast_stability_result=svc._build_fast_stability_result,
        run_with_timeout=svc._run_with_timeout,
        inject_orch_scheduled_stubs=svc._inject_orch_scheduled_stubs,
        execute_master_decisions=svc._execute_master_decisions,
        maintain_mlto_theses_for_session=svc._maintain_mlto_theses_for_session,
        write_qaa_v3_forced_decision_logs=write_qaa_v3_forced_decision_logs,
    )


def run_analyst_system_v3(
    session_id: str,
    session_status: str,
    session_orm_id: int,
    account_id: int,
    active_ids: list,
    market_summary: dict,
    host: AnalystV3Host,
) -> None:
    from backend.core.tenant import set_system_identity
    # [2026-08-04 修复] QAA v3 由 qaa_v3_tick_cycle 的 _async_llm 裸 threading.Thread
    # 派发执行，线程不继承调用线程的 ContextVar（set_system_identity 只在主循环设过）。
    # 本函数内部开 SessionLocal 查 Account / PaperPosition / LLMConfiguration 等租户表，
    # 无 admin 身份时 RLS fail-closed → LLM 配置解析返回 None → thesis 走规则回退
    # （日志：`[LLM] account=14 无归属用户` + `[MLTO:thesis_update] 无 LLM 配置`）。
    # 这里在函数入口设 system identity，覆盖整轮含 maintain_mlto_theses_for_session。
    set_system_identity()
    from backend.database.connection import SessionLocal
    from backend.database.models import (
        FullAutoSession,
        Account as _AcctModel,
        AIStrategy as _AIStrategy,
        StrategyMemory as _StrategyMemory,
    )
    from backend.services.trading_analysts import analyst_system, merge_reports_with_tier_slices

    # ── 数据采集：短生命周期 session ──
    positions_list = []
    bal_info = {}
    strategies_for_analysis = []
    _eff_acct = None
    mode = session_status

    _db_collect = SessionLocal()
    _track_key = f"{session_id}:analyst_v3_collect"
    host.active_db_sessions[_track_key] = _db_collect
    try:
        session_row = _db_collect.query(FullAutoSession).filter(
            FullAutoSession.id == session_orm_id
        ).first()
        if not session_row:
            return

        _trading_acct_id = account_id
        account = _db_collect.query(_AcctModel).filter(_AcctModel.id == session_row.account_id).first()
        if not account:
            return

        _eff_acct = (
            _db_collect.query(_AcctModel).filter(_AcctModel.id == _trading_acct_id).first()
            if _trading_acct_id != getattr(account, "id", None) else account
        )

        from backend.services.paper_trading_engine import paper_engine
        positions_list = paper_engine.get_positions(_db_collect, _eff_acct.id) or []
        bal_info = paper_engine.get_balance(_db_collect, _eff_acct.id) or {}

        from backend.services.strategy_analysis_context import (
            build_strategy_meta_cache,
            build_strategies_for_analysis,
            enrich_positions_with_strategy_meta,
        )
        pos_strat_ids = [p.get("strategy_id") for p in positions_list if p.get("strategy_id")]
        _strat_meta_cache = build_strategy_meta_cache(_db_collect, pos_strat_ids)
        enrich_positions_with_strategy_meta(positions_list, _strat_meta_cache)

        if active_ids:
            strategies_for_analysis = build_strategies_for_analysis(_db_collect, list(active_ids))
            for s in strategies_for_analysis:
                s.setdefault("target_symbols", [])
    except Exception as e:
        logger.error(f"[FullAuto][QAA v3] 数据采集异常: {e}", exc_info=True)
    finally:
        host.active_db_sessions.pop(_track_key, None)
        _db_collect.close()

    # ── LLM 调用：不持有任何 DB session ──
    _qaa_timeout_used: Optional[float] = None
    try:
        symbols_for_analysis = list(set(
            [p.get("symbol", "") for p in positions_list if p.get("symbol")]
            + list(market_summary.keys())
        ))

        # AI 精选币打标：让 Master LLM 看到选币评分并差异化加权
        host.annotate_auto_coin_meta(session_id, market_summary)

        _wins = sum(1 for p in positions_list if float(p.get("unrealized_pnl", 0) or 0) > 0) if positions_list else 0

        def _run_full_analysis(_llm_db):
            logger.info(
                f"[FullAuto][QAA v3] 开始真实 LLM 分析: "
                f"symbols={symbols_for_analysis}, positions={len(positions_list)}, "
                f"factor_in_prompt={'db' if _llm_db else 'market_summary'}"
            )
            return analyst_system.run_full_analysis(
                positions=positions_list,
                market_envs=market_summary,
                intel_data={},
                balance=bal_info,
                session_stats={
                    "account_equity": float(bal_info.get("total_equity", 0) or 0),
                    "daily_pnl": float(bal_info.get("daily_pnl", 0) or 0),
                    "total_trades": len(positions_list),
                    "win_rate": _wins / max(len(positions_list), 1),
                    "mode": mode,
                },
                strategies=strategies_for_analysis,
                symbols=symbols_for_analysis,
                mode=mode,
                db=_llm_db,
                account_id=account_id,
            )

        def _run_full_analysis_with_db():
            _db_llm = SessionLocal()
            try:
                return _run_full_analysis(_db_llm)
            finally:
                try:
                    _db_llm.close()
                except Exception:
                    pass

        from backend.config.settings import compute_qaa_analyst_timeout, QAA_ANALYST_STREAM_SAFETY_CAP_S
        from backend.services.llm_config_service import (
            get_llm_config_for_analysis,
            should_use_llm_streaming,
        )

        _llm_cfg = get_llm_config_for_analysis(account_id)
        _use_stream = should_use_llm_streaming(_llm_cfg)

        if _use_stream:
            _cap = QAA_ANALYST_STREAM_SAFETY_CAP_S
            logger.info(
                f"[FullAuto][QAA v3] 深度推理流式模式：等待 SSE [DONE] 自然结束"
                f"{f'，防挂死上限={_cap:.0f}s' if _cap > 0 else '（无外层固定窗口）'}"
            )
            if _cap > 0:
                _qaa_timeout_used = _cap
                result = host.run_with_timeout(
                    _run_full_analysis_with_db,
                    timeout_s=_cap,
                    fallback=None,
                    label="qaa_v3_run_full_analysis_stream",
                )
            else:
                result = _run_full_analysis_with_db()
        else:
            _qaa_timeout_used = compute_qaa_analyst_timeout(
                symbol_count=len(symbols_for_analysis),
                account_id=account_id,
            )
            logger.info(
                f"[FullAuto][QAA v3] 快速模型固定窗口={_qaa_timeout_used:.0f}s, "
                f"symbols={len(symbols_for_analysis)}"
            )
            result = host.run_with_timeout(
                _run_full_analysis_with_db,
                timeout_s=_qaa_timeout_used,
                fallback=None,
                label="qaa_v3_run_full_analysis",
            )
    except Exception as e:
        logger.error(f"[FullAuto][QAA v3] LLM 分析异常: {e}", exc_info=True)
        result = None

    if not result:
        logger.warning(
            "[FullAuto][QAA v3] 真实 LLM 分析未返回结果，写入超时降级 hold 决策，"
            "避免主循环和前端日志再次卡死"
        )
        result = host.build_fast_stability_result(
            symbols_for_analysis,
            trigger="timeout",
            timeout_s=_qaa_timeout_used,
        )
        result["_qaa_forced_log"] = True
        # 不再直接 return（2026-06-18）：即使 AI 超时，ScalpRouter 独立扫描
        # 仍应执行——短线层不依赖 AI，因子引擎能自主产生信号。

    # ── 交易执行：短生命周期 session ──
    _db_exec = SessionLocal()
    _track_key_exec = f"{session_id}:analyst_v3_exec"
    host.active_db_sessions[_track_key_exec] = _db_exec
    try:
        session_row = _db_exec.query(FullAutoSession).filter(
            FullAutoSession.id == session_orm_id
        ).first()
        if not session_row:
            return

        account = _db_exec.query(_AcctModel).filter(_AcctModel.id == session_row.account_id).first()
        if not account:
            return

        _trading_acct_id = account_id
        _eff_acct = (
            _db_exec.query(_AcctModel).filter(_AcctModel.id == _trading_acct_id).first()
            if _trading_acct_id != getattr(account, "id", None) else account
        )

        # 从 run_full_analysis 结果中提取 master_decision → decisions
        master_decision = result.get("master_decision", {}) if isinstance(result, dict) else {}
        decisions = master_decision.get("decisions", []) if isinstance(master_decision, dict) else []
        # Fix18: QAA v3 与 legacy 统一走 orchestrator 调度桩
        try:
            decisions = host.inject_orch_scheduled_stubs(
                decisions, market_summary or {}, session=session_row,
            )
        except Exception as _fix18_err:
            logger.debug(f"[Fix18][QAA v3] 独立调度异常(非致命): {_fix18_err}")
        # MidLong v2 C7：禁止每轮清空 mlto_handled_keys
        # 短线因子独立扫描由 APScheduler fullauto_scalp_* 负责，此处不再重复

        if decisions:
            host.execute_master_decisions(
                _db_exec, session_row, _trading_acct_id, decisions, positions_list,
                active_ids, market_summary, mode,
                analyst_reports=merge_reports_with_tier_slices(result),
                balance_info=bal_info,
            )
            if result.get("_qaa_forced_log"):
                host.write_qaa_v3_forced_decision_logs(
                    session_orm_id=session_orm_id,
                    account_id=_trading_acct_id,
                    decisions=decisions,
                    balance_info=bal_info,
                    positions_list=positions_list,
                    market_summary=market_summary,
                )
        try:
            host.maintain_mlto_theses_for_session(
                session=session_row,
                market_summary=market_summary or {},
                analyst_reports=merge_reports_with_tier_slices(result),
                mode=mode,
                portfolio={"balance": bal_info, "positions": positions_list},
            )
        except Exception as _mlto_maint_qaa:
            logger.debug("[MLTO][QAA v3] maintain: %s", _mlto_maint_qaa)
    except Exception as e:
        logger.error(f"[FullAuto][QAA v3] 交易执行异常: {e}", exc_info=True)
    finally:
        host.active_db_sessions.pop(_track_key_exec, None)
        _db_exec.close()
