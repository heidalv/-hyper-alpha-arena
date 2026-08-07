"""Paper 模拟下单执行 — 从 monolith _execute_paper_trade 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Set

from sqlalchemy.orm import Session

from backend.services.full_auto.paper_nature import resolve_sub_tier_and_nature
from backend.services.full_auto.paper_tp_sl import finalize_open_tp_sl

logger = logging.getLogger(__name__)


@dataclass
class PaperExecutionHost:
    """monolith 状态与回调切片，供 execute_paper_trade 使用。"""

    market_scan_cache: Dict[str, Any]
    template_recent_opens: Dict[str, Any]
    recovery_until: Dict[str, float]
    recovery_position_scale: float
    valid_trade_natures: Set[str]
    sub_mgr: Any
    ensure_bound_strategy: Callable
    get_trading_account_id: Callable
    extract_ai_position_pct: Callable
    apply_auto_coin_position_scale: Callable
    append_event: Callable
    get_today_realized_pnl: Callable
    get_validated_trade_nature: Callable
    recover_db_session: Callable
    is_unified_executor_on: Callable


def build_paper_execution_host(svc) -> PaperExecutionHost:
    if not hasattr(svc, "_template_recent_opens"):
        svc._template_recent_opens = {}
    return PaperExecutionHost(
        market_scan_cache=svc._market_scan_cache,
        template_recent_opens=svc._template_recent_opens,
        recovery_until=svc._recovery_until,
        recovery_position_scale=svc._RECOVERY_POSITION_SCALE,
        valid_trade_natures=svc._VALID_TRADE_NATURES,
        sub_mgr=svc._sub_mgr,
        ensure_bound_strategy=svc._ensure_bound_strategy,
        get_trading_account_id=svc._get_trading_account_id,
        extract_ai_position_pct=svc._extract_ai_position_pct,
        apply_auto_coin_position_scale=svc._apply_auto_coin_position_scale,
        append_event=svc._append_event,
        get_today_realized_pnl=svc._get_today_realized_pnl,
        get_validated_trade_nature=svc._get_validated_trade_nature,
        recover_db_session=svc._recover_db_session,
        is_unified_executor_on=svc._is_unified_executor_on,
    )


def execute_paper_trade(
    db: Session,
    session,
    strat,
    decision: dict,
    host: PaperExecutionHost,
) -> bool:
    """通过仓位管理器 + paper_engine 执行模拟下单。

    使用全新 DB 连接执行，避免上游 LLM 长调用期间事务损坏。
    """
    from backend.database.connection import SessionLocal as _FreshDB
    from backend.database.models import FullAutoSession as _FAS
    _session_id = getattr(session, "session_id", None)

    for _attempt in range(3):  # 最多重试 3 次
        fresh_db = _FreshDB()
        # 每次重试都重新查询 session（避免 detach）
        fresh_session = None
        if _session_id:
            try:
                fresh_session = fresh_db.query(_FAS).filter(_FAS.session_id == _session_id).first()
            except Exception:
                pass
        if not fresh_session:
            fresh_session = session
        db = fresh_db

        result = _execute_paper_trade_inner(db, fresh_session, strat, decision, host)
        if result is not None:
            try: fresh_db.close()
            except: pass
            return result
        # result=None 表示 DB 事务损坏，需要重建 db 重试
        logger.warning(f"[FullAuto] paper_trade DB 事务损坏，第 {_attempt+1} 次重建重试")
        try: fresh_db.close()
        except: pass

    return False


def _execute_paper_trade_inner(db: Session, session, strat, decision: dict, host: PaperExecutionHost):
    """实际执行下单。返回 True/False，DB 损坏时返回 None 触发外层重试。

    全程禁用 autoflush：禁止 SQLAlchemy 在查询时自动 flush pending writes，
    避免与并发的 _paper_tick / scalp_loop 等线程的 DB 写入冲突导致 InFailedSqlTransaction。
    所有写入操作在最后通过显式 db.commit() 统一提交。
    """
    _prev_autoflush = db.autoflush
    db.autoflush = False
    try:
        from backend.services.paper_trading_engine import paper_engine
        from backend.services.position_memory_manager import position_manager

        strat = host.ensure_bound_strategy(db, strat)
        if strat is None:
            logger.warning("[FullAuto] _execute_paper_trade: 策略对象无效或已 detach")
            return False

        symbol = strat.primary_symbol
        if not decision.get("_orchestrator_context"):
            _ms = host.market_scan_cache.get(str(symbol).upper(), {})
            if isinstance(_ms, dict):
                decision["_orchestrator_context"] = _ms.get("orchestrator") or {}

        # ════════════════════════════════════════════════════════════════
        # P5-fix(2026-05-08) — 同模板齐发限流
        # 病根：tpl_short_range 一个模板在同一时刻给 BTC/ETH/SOL/XPL 都触发 SELL，
        #       AI 全部跟单 → tier 预算被一窝蜂占满，且单一信号源风险集中。
        # 规则：同一个 source_template_id，5 分钟内最多在 2 个 symbol 上触发开仓；
        #       第 3 个起被拦截，让信号分散到下一轮再评估。
        # ════════════════════════════════════════════════════════════════
        try:
            _action_for_dedupe = (decision.get("action") or "").lower()
            if _action_for_dedupe in ("buy", "sell"):
                _src_tpl = (
                    (strat.genome or {}).get("source_template_id")
                    or getattr(strat, "parent_template_id", None)
                    or ""
                )
                if _src_tpl:
                    if not hasattr(self, "_template_recent_opens"):
                        host.template_recent_opens = {}  # tpl_id → [(ts, symbol, side), ...]
                    _now = time.time()
                    _recent = [
                        t for t in host.template_recent_opens.get(_src_tpl, [])
                        if _now - t[0] < 300  # 5 分钟窗口
                    ]
                    # 同向 + 不同 symbol 计数
                    _same_dir_syms = {
                        t[1] for t in _recent
                        if t[2] == _action_for_dedupe and t[1] != symbol
                    }
                    if len(_same_dir_syms) >= 2:
                        host.append_event(
                            session, "template_burst_block",
                            f"💥 同模板齐发拦截 {symbol} {_action_for_dedupe.upper()}: "
                            f"模板 {_src_tpl} 5分钟内已在 {sorted(_same_dir_syms)} "
                            f"开 {_action_for_dedupe} 仓 ≥2 次，拒绝第 3 单"
                        )
                        logger.warning(
                            f"[FullAuto|TplBurst] 拦截 {symbol} {_action_for_dedupe} "
                            f"(模板={_src_tpl}, 已发={sorted(_same_dir_syms)})"
                        )
                        return False
                    # 通过拦截 → 把本次开仓登记进窗口（在真正下单成功后还要再确认一次，但先占位）
                    host.template_recent_opens[_src_tpl] = _recent + [
                        (_now, symbol, _action_for_dedupe)
                    ]
        except Exception as _burst_err:
            logger.debug(f"[FullAuto] 同模板齐发限流跳过: {_burst_err}")
        # P5-fix(2026-05-08): paper模式资金池/下单必须用 paper_account_id (而非策略上记录的 account_id)。
        # 策略 account_id 历史上写的是 session.account_id（实盘主账户ID），导致所有 paper 下单
        # 都落在了 "实盘账户的 paper 影子" 而非真正的 PAPER 账户，前端查 PAPER 账户看到空。
        _trading_mode = (getattr(session, "trading_mode", "") or "").lower()
        if _trading_mode == "paper":
            account_id = host.get_trading_account_id(db, session)
        else:
            account_id = strat.account_id
        action = decision.get("action", "")
        side = decision.get("side", "buy" if "long" in action or action == "buy" else "sell")
        price = decision.get("price", 0)
        leverage = decision.get("leverage", 10)
        confidence_pct = decision.get("confidence_pct", 0)
        stop_loss = decision.get("stop_loss_price", 0)
        take_profit = decision.get("take_profit_price", 0)

        if not price:
            try:
                from backend.services.market_data import get_last_price
                price = get_last_price(symbol) or 0
            except Exception:
                pass

        if not price or price <= 0:
            host.append_event(session, "trade_skip", f"{symbol}: 无法获取价格")
            return False

        timeframe_tier = decision.get("timeframe_tier") or getattr(strat, "timeframe_tier", None) or "mid"

        # ── 仓位管理器评估 ──
        # 提前解析 trade_nature 用于仓位管理器隔离
        _pre_nature = decision.get("trade_nature") or ""
        _ai_pos_pct = host.extract_ai_position_pct(decision) or float(
            decision.get("position_pct", 0) or 0
        )
        plan = position_manager.evaluate_trade(
            db=db,
            account_id=account_id,
            symbol=symbol,
            side=side,
            ai_confidence=confidence_pct / 100.0 if confidence_pct > 1 else confidence_pct,
            current_price=price,
            signal_source=decision.get("signal_source", "rule_engine"),
            market_regime=decision.get("market_regime", "unknown"),
            volatility_pct=decision.get("volatility_pct", 0.015),
            raw_leverage=leverage,
            raw_position_pct=_ai_pos_pct,
            raw_notional_usd=float(decision.get("_sizing_notional_usd", 0) or 0),
            raw_margin_usd=float(decision.get("_sizing_margin_usd", 0) or 0),
            respect_raw_sizing=bool(decision.get("_respect_sizing_plan")),
            raw_tp_price=take_profit,
            raw_sl_price=stop_loss,
            strategy_id=strat.strategy_id,
            tier=timeframe_tier,
            trade_nature=_pre_nature,
            orchestrator_context=decision.get("_orchestrator_context"),
        )

        # ── 恢复模式：退出防守后的过渡期，缩减仓位 ──
        recovery_ts = host.recovery_until.get(session.session_id, 0)
        if recovery_ts > 0 and time.time() < recovery_ts:
            scale = host.recovery_position_scale
            if plan.action in ("open", "close_and_open") and hasattr(plan, "margin_usd"):
                plan.margin_usd = round(plan.margin_usd * scale, 2)
                plan.notional_usd = round(plan.notional_usd * scale, 2)
                remaining_min = (recovery_ts - time.time()) / 60
                logger.info(f"[FullAuto] 恢复期仓位缩减: {symbol} margin×{scale} "
                            f"(剩余{remaining_min:.0f}min)")
        elif recovery_ts > 0 and time.time() >= recovery_ts:
            host.recovery_until.pop(session.session_id, None)
            host.append_event(session, "recovery_complete",
                f"[OK] 恢复期结束，回到正常仓位")

        host.apply_auto_coin_position_scale(
            db, session, account_id, symbol, plan,
        )

        # ── 执行计划 ──
        if plan.action == "skip":
            _skip_detail = (plan.reasoning or "未知原因")[:200]
            logger.info(
                "[FullAuto][ExecSkip] %s %s source=PosMgr reason=%s",
                symbol, side, _skip_detail,
            )
            _severity = "critical" if "冻结" in _skip_detail else "warning"
            host.append_event(
                session, "trade_skip",
                f"⏭️ {symbol} {side}: {_skip_detail}",
                severity=_severity,
            )
            if "冻结" in _skip_detail or "frozen" in _skip_detail.lower():
                host.append_event(
                    session, "mental_frozen_block",
                    f"🧊 连亏保护：{symbol} {side} 被心理状态机拦截 — {_skip_detail}",
                    severity="critical",
                )
            return False

        # 需要先平反向仓位
        if plan.action == "close_and_open" and plan.close_opposite_side:
            # ── P2 D13: long tier 对 ai_reverse 免疫 ──
            try:
                from backend.services.risk_band_resolver import stage_e_active
                from backend.config.settings import RISK_USE_LONG_TIER_IMMUNE
                if stage_e_active() and RISK_USE_LONG_TIER_IMMUNE:
                    _opp_tier = None
                    for _pp in paper_engine.get_positions(db, account_id) or []:
                        if _pp.get("symbol") == symbol and _pp.get("side") == plan.close_opposite_side:
                            _opp_tier = (_pp.get("timeframe_tier") or "mid").strip().lower()
                            break
                    if _opp_tier == "long":
                        logger.info(
                            f"[FullAuto][StageE][P2.D13] {symbol}[long] ai_reverse 被免疫拦截，"
                            f"不平反向 long 仓"
                        )
                        host.append_event(session, "long_tier_reverse_immune",
                            f"🛡️ {symbol} long tier 免疫 ai_reverse，不平反向长仓")
                        return False
            except Exception as _e_imm2:
                logger.debug(f"[FullAuto][StageE][P2.D13] ai_reverse immune 异常: {_e_imm2}")

            close_result = paper_engine.close_position(
                db, account_id, symbol, plan.close_opposite_side,
                reason="ai_reverse"
            )
            if close_result:
                pnl = close_result.get("pnl", 0)
                closed_fully = close_result.get("closed_fully", True)
                session.total_trades = (session.total_trades or 0) + 1
                host.append_event(session, "trade_executed",
                    f"{symbol} 平{plan.close_opposite_side}仓 PnL=${pnl:+.2f}")
                # ── P3 M2: 记录 ai_reverse 时间，为后续 60min 冷却服务 ──
                try:
                    from backend.services.reentry_cooldown import record_ai_reverse
                    record_ai_reverse(account_id, symbol)
                except Exception:
                    pass
                if not closed_fully:
                    remaining = close_result.get("remaining_size", 0)
                    host.append_event(session, "trade_warning",
                        f"{symbol} 反向开仓中止: 旧仓位未完全平掉, 残留={remaining:.6f}")
                    logger.warning(f"[FullAuto] {symbol} close_and_open 中止: remaining={remaining}")
                    return False
            else:
                host.append_event(session, "trade_failed",
                    f"{symbol} 平{plan.close_opposite_side}仓失败")
                return False

        # ── 统一风控（深挖第 3 轮 2026-05-08：UnifiedRiskGate）──
        try:
            # 风控查询前 flush pending writes（get_balance 会修改 position 的 mark_price，
            # 如果 pending 写入冲突会导致 InFailedSqlTransaction）
            try:
                db.flush()
            except Exception:
                db.rollback()
            from backend.services.unified_risk_gate import unified_check
            _bal_info = paper_engine.get_balance(db, account_id) or {}
            _open_positions = paper_engine.get_positions(db, account_id)
            _existing = [
                {
                    "symbol": p["symbol"], "side": p["side"],
                    "margin": float(p.get("margin", 0)),
                    "notional": float(p.get("size", 0)) * float(p.get("mark_price", 0)),
                    "size": float(p.get("size", 0)),
                    "leverage": float(p.get("leverage", 10)),
                }
                for p in _open_positions
            ]
            _equity = _bal_info.get("total_equity", 10000)
            _avail = _bal_info.get("available_balance", _equity)
            _frozen = _bal_info.get("frozen_margin", 0)
            _margin_pct = (_frozen / _equity * 100.0) if _equity > 0 else 0.0
            _ures2 = unified_check(
                db=db, account_id=account_id,
                symbol=symbol, side=side,
                notional=plan.notional_usd, margin=plan.margin_usd, leverage=plan.leverage,
                total_equity=_equity, available_balance=_avail, frozen_margin=_frozen,
                realized_pnl_today=host.get_today_realized_pnl(db, account_id),
                margin_usage_percent=_margin_pct,
                existing_positions=_existing,
                op_source="full_auto:execute_strategy",
            )
            # 风控检查可能 flush 了日志写入，确保不影响后续下单事务
            try:
                db.rollback()  # 回滚风控的 flush（只保留内存中的决策数据）
            except Exception:
                pass
            if not _ures2.passed:
                host.append_event(session, f"{_ures2.blocked_layer}_block",
                    f"{symbol} {side}: {_ures2.reason_text} "
                    f"[layer={_ures2.blocked_layer} rule={_ures2.blocked_rule}]")
                logger.info(f"[FullAuto] 风控拦截: {_ures2.reason_text}")
                return False
        except Exception as _rg_err:
            # [fix] rollback 避免 InFailedSqlTransaction 污染后续交易
            try:
                db.rollback()
            except Exception:
                pass
            logger.warning(f"[FullAuto] 统一风控异常(拦截): {_rg_err}")
            return False

        _trade_nature, _sub_tier = resolve_sub_tier_and_nature(
            strat=strat,
            decision=decision,
            timeframe_tier=timeframe_tier,
            symbol=symbol,
            market_scan_cache=host.market_scan_cache,
            get_validated_trade_nature=host.get_validated_trade_nature,
            valid_trade_natures=host.valid_trade_natures,
        )

        # ── 子仓位管理器审核开仓 ──
        if host.sub_mgr:
            try:
                _tp_pct = 0.0
                if plan.take_profit_price and price > 0:
                    _tp_pct = abs(plan.take_profit_price - price) / price
                _bal_info_sub = paper_engine.get_balance(db, account_id) or {}
                _sub_ok, _sub_reason = host.sub_mgr.review_open(
                    db=db, account_id=account_id, symbol=symbol,
                    side=side, trade_nature=_trade_nature,
                    notional_usd=plan.notional_usd, tp_pct=_tp_pct,
                    total_equity=float(_bal_info_sub.get("total_equity", 0)),
                    agent_independent=bool(decision.get("_agent_independent")),
                )
                if not _sub_ok:
                    host.append_event(session, "sub_pos_blocked",
                        f"{symbol}[{_trade_nature}] 子仓审核拦截: {_sub_reason}")
                    return False
            except Exception as _sub_err:
                logger.debug(f"[FullAuto] 子仓审核跳过: {_sub_err}")
                try:
                    db.rollback()
                except Exception:
                    pass

        # 开新仓
        quantity = plan.notional_usd / price if price > 0 else 0
        if quantity <= 0:
            host.append_event(session, "trade_skip",
                f"{symbol}: 计算数量为0")
            return False

        _is_auto_coin = False
        try:
            from backend.services.auto_coin_policy import applies_strict_auto_coin_rules
            _is_auto_coin = applies_strict_auto_coin_rules(
                symbol, getattr(session, "auto_coin_symbols", None) or [],
            )
        except Exception:
            pass

        _final_sl, _final_tp = finalize_open_tp_sl(
            symbol=symbol,
            trade_nature=_trade_nature,
            side=side,
            price=price,
            plan_sl=plan.stop_loss_price,
            plan_tp=plan.take_profit_price,
            is_auto_coin=_is_auto_coin,
            on_event=lambda et, msg: host.append_event(session, et, msg),
        )

        _plan_hold_h = float(
            getattr(plan, "expected_hold_hours", 0)
            or (decision.get("expected_hold_hours") if isinstance(decision, dict) else 0)
            or 0
        )
        host.recover_db_session(db, label="place_order")

        _pos_meta = {}
        _env = (decision or {}).get("_agent_envelope")
        if isinstance(_env, dict) and _env.get("agent_source"):
            _pos_meta = {
                "agent_envelope": _env,
                "agent_source": _env.get("agent_source"),
                "alignment_score": _env.get("alignment_score"),
                "cited_fact_ids": _env.get("cited_fact_ids"),
            }

        # 阶段 3 统一执行器开关: USE_UNIFIED_EXECUTOR=true 时走 PaperExecutor
        # （封装 paper_engine + trace_id 注入 + 返回值标准化），否则走原路径
        if host.is_unified_executor_on():
            from backend.services.exchange.executors import OrderContext
            from backend.services.exchange.paper_executor import PaperExecutor
            _ctx = OrderContext(
                account_id=account_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type="market",
                price=price,
                leverage=plan.leverage,
                sl_price=_final_sl,
                tp_price=_final_tp,
                strategy_id=strat.strategy_id,
                timeframe_tier=_sub_tier,
                trade_nature=_trade_nature,
                expected_hold_hours=_plan_hold_h if _plan_hold_h > 0 else None,
                # 阶段 3.2: 执行算法透传（决策层可选产出 algo/algo_config）
                algo=(decision or {}).get("algo", "MARKET"),
                algo_config=(decision or {}).get("algo_config"),
                position_metadata=_pos_meta or None,
            )
            _ores = PaperExecutor().place_order(db, _ctx)
            # 兼容旧代码: 从 OrderResult.raw 提取原始 dict（保持 result.get("status") 可用）
            result = _ores.raw if _ores.raw else _ores.to_dict()
            if _ores.success and "status" not in result:
                result["status"] = "filled"
            logger.debug(
                f"[FullAuto] 统一执行器下单: {symbol} {side} → status={_ores.status} "
                f"order_id={_ores.order_id}"
            )
        else:
            result = paper_engine.place_order(
                db=db,
                account_id=account_id,
                symbol=symbol,
                side=side,
                quantity=quantity,
                price=price,
                leverage=plan.leverage,
                sl_price=_final_sl,
                tp_price=_final_tp,
                order_type="market",
                strategy_id=strat.strategy_id,
                timeframe_tier=_sub_tier,
                trade_nature=_trade_nature,
                expected_hold_hours=_plan_hold_h if _plan_hold_h > 0 else None,
                position_metadata=_pos_meta or None,
            )

        if result and result.get("status") == "filled":
            session.total_trades = (session.total_trades or 0) + 1
            if _trade_nature in ("trend_follow", "position"):
                try:
                    from backend.services.trend_prediction_service import trend_prediction_service
                    _ta = (decision or {}).get("_trend_analysis") or {}
                    _pos_id = result.get("position_id") or result.get("order_id")
                    trend_prediction_service.create_from_analysis(
                        symbol=symbol,
                        paper_position_id=_pos_id,
                        entry_price=float(price or 0),
                        analysis=_ta,
                    )
                except Exception as _tpr_err:
                    logger.debug(f"[TrendPrediction] 开仓落库跳过: {_tpr_err}")
            host.append_event(session, "trade_executed",
                f"{symbol}[{_trade_nature}] {side.upper()} @ ${price:,.2f} x{quantity:.6f} "
                f"lev={plan.leverage}x margin=${plan.margin_usd:.0f} | "
                f"TP=${plan.take_profit_price:.2f} SL=${plan.stop_loss_price:.2f} | "
                f"{plan.reasoning[:80]}")
            logger.info(
                f"[FullAuto] 模拟下单成功: {symbol}[{_trade_nature}] {side} @ {price} "
                f"lev={plan.leverage}x margin=${plan.margin_usd:.0f}"
            )
            # ── 飞书通知：开仓事件 ──
            try:
                from backend.services.openclaw_notify import notify_trade_open
                import asyncio as _nf_asyncio
                _nf_coro = notify_trade_open(
                    symbol=symbol, side=side, price=price,
                    leverage=plan.leverage, margin=plan.margin_usd,
                    confidence=plan.confidence if hasattr(plan, 'confidence') else 0,
                    strategy=plan.reasoning[:40] if plan.reasoning else "",
                    tp=plan.take_profit_price, sl=plan.stop_loss_price,
                )
                try:
                    _nf_loop = _nf_asyncio.get_running_loop()
                    _nf_loop.create_task(_nf_coro)
                except RuntimeError:
                    _nf_asyncio.run(_nf_coro)
            except Exception as _nf_err:
                logger.debug(f"[FullAuto] 开仓通知发送失败(非致命): {_nf_err}")
            # 记录开仓时的信号快照（信号反馈闭环）
            try:
                from backend.services.signal_feedback_tracker import signal_feedback_tracker
                from backend.services.intelligence_signal_engine import IntelligenceSignalEngine
                _engine = IntelligenceSignalEngine()
                _sig = _engine.compute_trading_signal(symbol)
                _active_signals = {}
                if _sig.funding:
                    _active_signals["funding"] = {"direction": _sig.funding.signal, "value": _sig.funding.rate}
                if _sig.oi:
                    _active_signals["oi"] = {"direction": _sig.oi.signal, "value": _sig.oi.oi_change_pct}
                if _sig.liquidation:
                    _active_signals["liquidation"] = {"direction": _sig.liquidation.signal, "value": 0}
                if abs(_sig.whale_direction) > 0.1:
                    _active_signals["whale"] = {"direction": "bullish" if _sig.whale_direction > 0 else "bearish", "value": _sig.whale_direction}
                if abs(_sig.news_sentiment) > 0.1:
                    _active_signals["news"] = {"direction": "bullish" if _sig.news_sentiment > 0 else "bearish", "value": _sig.news_sentiment}
                _active_signals["fear_greed"] = {"direction": "neutral", "value": _sig.fear_greed_index}
                if _sig.long_short_ratio != 1.0:
                    _active_signals["long_short"] = {"direction": "bullish" if _sig.long_short_ratio > 1 else "bearish", "value": _sig.long_short_ratio}
                if _sig.top_trader_ls_ratio != 1.0:
                    _active_signals["top_trader"] = {"direction": "bullish" if _sig.top_trader_ls_ratio > 1 else "bearish", "value": _sig.top_trader_ls_ratio}
                # V3 整合: 计算因子快照一并记录
                _factor_vals = None
                try:
                    from backend.services.factor_engine import factor_engine as _fe
                    from backend.services.market_data import get_kline_data
                    _fv_raw = get_kline_data(symbol, period="15m", count=100)
                    if _fv_raw:
                        import pandas as _pd
                        _fv_df = _pd.DataFrame(_fv_raw)
                        _fvals = _fe.compute_all_factors(_fv_df)
                        if _fvals:
                            _factor_vals = {
                                k: (v.value if hasattr(v, "value") else float(v))
                                for k, v in _fvals.items()
                            }
                except Exception:
                    pass
                trade_id = result.get("position_id") or result.get("order_id")
                signal_feedback_tracker.record_entry_signals(
                    db, account_id, trade_id, symbol, side, _active_signals,
                    factor_values=_factor_vals)
            except Exception as _sf_err:
                logger.debug(f"[FullAuto] 信号快照记录失败(非致命): {_sf_err}")

            # 回写 ai_decision_logs.executed = true
            _log_id = decision.get("_decision_log_id")
            if _log_id:
                try:
                    from backend.services.ai_decision_service import mark_decision_executed
                    mark_decision_executed(db, _log_id, strat.strategy_id)
                except Exception:
                    pass
            return True
        elif result and result.get("status") == "pending":
            host.append_event(session, "trade_pending",
                f"{symbol} {side} 限价单已挂出")
            return True
        else:
            err = result.get("error", "unknown") if result else "engine returned None"
            host.append_event(session, "trade_failed",
                f"{symbol} {side} 下单失败: {err}")
            logger.warning(f"[FullAuto] 模拟下单失败: {symbol} {err}")
            return False
    except Exception as e:
        err_str = str(e)
        if "InFailedSqlTransaction" in err_str or "server closed" in err_str or "not bound to a Session" in err_str or "not persistent" in err_str:
            try: db.close()
            except: pass
            return None
        logger.error(f"[FullAuto] 模拟交易执行异常: {e}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        host.append_event(session, "trade_error", f"{getattr(strat, 'primary_symbol', '?')}: {str(e)[:80]}")
        return False
    finally:
        db.autoflush = _prev_autoflush
        try:
            db.close()
        except Exception:
            pass
