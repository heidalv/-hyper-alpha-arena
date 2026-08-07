"""AI 决策执行 — 从 monolith _execute_ai_decisions 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class AiDecisionsHost:
    """monolith 状态与回调切片。"""

    last_unified_snapshot: Any = None

    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    expand_multi_tier_decisions: Callable = field(repr=False, default=lambda *a, **k: [])
    ensure_bound_strategy: Callable = field(repr=False, default=lambda *a, **k: None)
    resolve_decision_leverage: Callable = field(repr=False, default=lambda *a, **k: (10, ""))
    extract_ai_position_pct: Callable = field(repr=False, default=lambda *a, **k: None)
    resolve_alignment_scale: Callable = field(repr=False, default=lambda *a, **k: 1.0)
    execute_paper_trade: Callable = field(repr=False, default=lambda *a, **k: False)
    execute_live_trade: Callable = field(repr=False, default=lambda *a, **k: None)


def build_ai_decisions_host(svc) -> AiDecisionsHost:
    return AiDecisionsHost(
        last_unified_snapshot=getattr(svc, "_last_unified_snapshot", None),
        get_trading_account_id=svc._get_trading_account_id,
        append_event=svc._append_event,
        expand_multi_tier_decisions=svc._expand_multi_tier_decisions,
        ensure_bound_strategy=svc._ensure_bound_strategy,
        resolve_decision_leverage=svc._resolve_decision_leverage,
        extract_ai_position_pct=svc._extract_ai_position_pct,
        resolve_alignment_scale=svc._resolve_alignment_scale,
        execute_paper_trade=svc._execute_paper_trade,
        execute_live_trade=svc._execute_live_trade,
    )


def execute_ai_decisions(
    db: Session,
    session,
    active_ids: list,
    market_data: dict,
    host: AiDecisionsHost,
) -> None:
    from backend.database.models import AIStrategy as _AIStrategy, Account

    account = db.query(Account).filter(Account.id == session.account_id).first()
    if not account:
        logger.warning(f"[FullAuto] 找不到账户 {session.account_id}")
        return
    # 解析实际交易账户（模拟模式用 paper_account_id）
    _trading_acct_id = host.get_trading_account_id(db, session)
    # 注意：包含 active 和 paused 策略，扇出不应受暂停影响
    symbols = []
    strat_map = {}            # key: symbol -> 策略（保留向后兼容）
    strat_tier_map = {}      # key: (symbol, tier) -> 策略（多 nature 精确匹配）
    for sid in list(active_ids):
        strat = db.query(_AIStrategy).filter(
            _AIStrategy.strategy_id == sid,
            _AIStrategy.status.in_(["active", "paused"]),
        ).first()
        if strat and strat.primary_symbol:
            symbols.append(strat.primary_symbol)
            strat_map[strat.primary_symbol] = strat
            _s_tier = getattr(strat, 'timeframe_tier', None) or 'mid'
            strat_tier_map[(strat.primary_symbol.upper(), _s_tier)] = strat

    if not symbols:
        return

    symbols = list(set(symbols))

    # 构建 portfolio 和 prices
    try:
        from backend.services.paper_trading_engine import paper_engine
        from backend.services.market_data import get_last_price as get_latest_price

        bal_info = paper_engine.get_balance(db, _trading_acct_id) or {}
        available = bal_info.get("available", 0) or bal_info.get("total_equity", 10000) or 10000

        portfolio = {
            "cash": available,
            "frozen_cash": 0,
            "positions": {},
            "total_assets": bal_info.get("total_equity", available),
        }

        # 加载 paper 仓位
        positions_list = paper_engine.get_positions(db, _trading_acct_id) or []
        for pos in positions_list:
            psym = pos.get("symbol", "")
            portfolio["positions"][psym] = {
                "quantity": pos.get("quantity", 0),
                "avg_cost": pos.get("avg_price", 0),
                "current_value": pos.get("value", 0),
                "unrealized_pnl": pos.get("unrealized_pnl", 0),
                "leverage": pos.get("leverage", 10),
            }

        prices = {}
        for sym in symbols:
            p = get_latest_price(sym)
            if p:
                prices[sym] = p
    except Exception as e:
        logger.warning(f"[FullAuto] 构建决策上下文失败: {e}")
        portfolio = {"cash": 10000, "frozen_cash": 0, "positions": {}, "total_assets": 10000}
        prices = {}

    # 调用 LLM 决策
    try:
        from backend.services.ai_decision_service import (
            call_ai_for_decision, call_ai_for_decision_with_fallback,
            save_ai_decision, resolve_account_llm_config,
        )

        resolve_account_llm_config(db, account)

        # 构建 hyperliquid_state 供 Phase 3B 三维确认使用
        total_eq = portfolio.get("total_assets", 10000)
        hyperliquid_state = {
            "total_equity": total_eq,
            "available_balance": portfolio.get("cash", total_eq),
            "used_margin": portfolio.get("frozen_cash", 0),
            "margin_usage_percent": 0,
            "maintenance_margin": 0,
            "positions": [],
        }
        for psym, pdata in portfolio.get("positions", {}).items():
            hyperliquid_state["positions"].append({
                "coin": psym,
                "szi": pdata.get("quantity", 0),
                "entry_px": pdata.get("avg_cost", 0),
                "position_value": pdata.get("current_value", 0),
                "unrealized_pnl": pdata.get("unrealized_pnl", 0),
                "leverage": pdata.get("leverage", 10),
            })

        # 将编排器方向注入 trigger_context，供 Phase 3B fallback
        # 必须在因子融合预计算之前构建：融合逻辑依赖 orch_directions（修复原先 NameError 静默吞掉整段）
        orch_directions: Dict[str, dict] = {}
        if isinstance(market_data, dict):
            for sym in symbols:
                od = market_data.get(sym)
                if not od:
                    continue
                # OrchestratorDecision 对象（来自 quick eval）
                if hasattr(od, "final_side"):
                    orch_directions[sym] = {
                        "side": od.final_side,
                        "action": od.final_action,
                        "position_pct": od.final_position_pct,
                        "long_bias": od.long_view.bias,
                        "long_confidence": od.long_view.confidence,
                        "mid_bias": od.mid_view.bias,
                        "mid_confidence": od.mid_view.confidence,
                        "short_bias": od.short_view.bias,
                        "short_confidence": od.short_view.confidence,
                        "recommended_nature": getattr(od, "recommended_nature", "") or "",
                    }
                # market_summary dict（来自 health check）
                elif isinstance(od, dict) and "orchestrator" in od:
                    oc = od["orchestrator"]
                    orch_directions[sym] = {
                        "side": oc.get("side", ""),
                        "action": oc.get("action", ""),
                        "position_pct": oc.get("position_pct", 0),
                        "long_bias": oc.get("long_bias", "neutral"),
                        "long_confidence": oc.get("long_confidence", 0),
                        "mid_bias": oc.get("mid_bias", "neutral"),
                        "mid_confidence": oc.get("mid_confidence", 0),
                        "short_bias": oc.get("short_bias", "neutral"),
                        "short_confidence": oc.get("short_confidence", 0),
                        "recommended_nature": oc.get("recommended_nature", "") or "",
                    }

        # V3 整合: 获取因子融合决策作为补充判据
        fusion_verdicts: Dict[str, dict] = {}
        try:
            from backend.services.ai_decision_integration import compute_fusion_decision
            import pandas as pd
            _snap = getattr(self, "_last_unified_snapshot", None)
            _snap_klines = getattr(_snap, "klines", None) if _snap else None
            for _sym in symbols:
                _klines = None
                if _snap_klines:
                    _klines = _snap_klines.get((_sym, "15m"))
                    if _klines is None:
                        _klines = _snap_klines.get((_sym.upper(), "15m"))
                if _klines is None or (hasattr(_klines, "empty") and _klines.empty):
                    try:
                        from backend.services.kline_data_service import kline_service
                        _raw = kline_service.get_klines_from_db(_sym.upper(), "15m", 60)
                        _klines = pd.DataFrame(_raw) if _raw else None
                    except Exception:
                        _klines = None
                _pos_side = None
                _pos_data = portfolio.get("positions", {}).get(_sym)
                if _pos_data:
                    _qty = _pos_data.get("quantity", 0)
                    _pos_side = "long" if _qty > 0 else ("short" if _qty < 0 else None)
                _orch_action = None
                _od = orch_directions.get(_sym, {})
                if _od:
                    _orch_action = _od.get("action", "")
                _fusion = compute_fusion_decision(
                    symbol=_sym,
                    klines=_klines,
                    position_side=_pos_side,
                    orchestrator_action=_orch_action,
                )
                if _fusion:
                    fusion_verdicts[_sym] = _fusion
        except Exception as _fe:
            logger.warning(
                f"[FullAuto] 因子融合预计算失败(非致命): {_fe}",
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )

        # 确定是否有策略使用了进化后 Prompt，传递给决策引擎
        _evolved_strategy_id = None
        for _sym, _strat in strat_map.items():
            if getattr(_strat, "master_prompt_template_id", None):
                _evolved_strategy_id = _strat.strategy_id
                break

        decisions = call_ai_for_decision_with_fallback(
            db=db,
            account=account,
            portfolio=portfolio,
            prices=prices,
            symbols=symbols,
            hyperliquid_state=hyperliquid_state,
            trigger_context={
                "trigger_type": "autonomous",
                "source": "full_auto",
                "ai_strategy_id": _evolved_strategy_id,
                "orchestrator_directions": orch_directions,
                "fusion_verdicts": fusion_verdicts,  # V3 整合: 因子融合决策
            },
        )

        if not decisions:
            host.append_event(session, "ai_no_decision", f"LLM 未返回决策 (symbols={symbols})")
            logger.info(f"[FullAuto] LLM 无决策 {symbols}")
            return

        # ── 多周期扇出（复用独立方法） ──
        decisions = host.expand_multi_tier_decisions(
            decisions, strat_tier_map, orch_directions, session)

        # 先保存所有决策记录（executed=False），记住 log ID 以便后续标记
        _decision_log_ids: Dict[str, int] = {}  # key: "SYMBOL:TIER" 或 "SYMBOL"
        for dec in decisions:
            try:
                _sym = (dec.get("symbol") or "").upper()
                _tier = dec.get("_fan_tier", "")
                _log_key = f"{_sym}:{_tier}" if _tier else _sym
                # 扇出决策使用 strat_tier_map 获取正确的 strategy_id
                _strat_for_log = host.ensure_bound_strategy(
                    db,
                    strat_tier_map.get((_sym, _tier)) if _tier
                    else strat_map.get(_sym),
                    active_ids=active_ids,
                    status=("active", "paused"),
                )
                _log_id = save_ai_decision(db, account, dec, portfolio,
                    ai_strategy_id=_strat_for_log.strategy_id if _strat_for_log else None,
                    orchestrator_info=(market_data or {}).get(_sym, {}).get("orchestrator"))
                if _log_id and _log_key:
                    _decision_log_ids[_log_key] = _log_id
            except Exception as e:
                logger.debug(f"[FullAuto] 保存决策记录失败: {e}")

        for dec in decisions:
            sym = dec.get("symbol", "")
            operation = str(dec.get("operation", "hold")).lower()
            _raw_conf = dec.get("confidence", 0)
            # 归一化 confidence：旧路径可能是 0.0-1.0（LLM原始），也可能是 0-100（规则引擎覆盖）
            if _raw_conf and 0 < _raw_conf <= 1.0:
                confidence = round(_raw_conf * 100, 1)
            else:
                confidence = _raw_conf
            reasoning = dec.get("reasoning", "")

            host.append_event(session, "ai_decision",
                f"{sym}: {operation} 置信={confidence}% | {reasoning[:80]}")
            logger.info(f"[FullAuto] AI决策 {sym}: {operation}, conf={confidence}")

            if operation not in ("buy", "sell"):
                continue

            # V3 整合: 因子融合反向否决 — 如果融合决策方向与AI相反且置信度较高，跳过
            _fusion_v = fusion_verdicts.get(sym)
            if _fusion_v and _fusion_v.get("confidence", 0) > 0.5:
                _f_action = _fusion_v.get("action", "hold")
                if _f_action in ("sell", "close") and operation == "buy":
                    logger.info(
                        f"[FullAuto] 因子融合否决 {sym}: AI=buy 但融合={_f_action} "
                        f"(conf={_fusion_v['confidence']:.2f})"
                    )
                    host.append_event(session, "fusion_veto",
                        f"{sym}: 因子融合否决AI买入(融合={_f_action})")
                    continue
                elif _f_action in ("buy", "close") and operation == "sell":
                    logger.info(
                        f"[FullAuto] 因子融合否决 {sym}: AI=sell 但融合={_f_action} "
                        f"(conf={_fusion_v['confidence']:.2f})"
                    )
                    host.append_event(session, "fusion_veto",
                        f"{sym}: 因子融合否决AI卖出(融合={_f_action})")
                    continue

            if confidence < 5:
                host.append_event(session, "ai_skip",
                    f"{sym}: 置信度过低({confidence}%<5%)，跳过")
                continue

            # 解析 trade_nature（先解析，用于策略匹配）
            from backend.services.sub_position_manager import normalize_nature
            _dec_nature_raw = dec.get("trade_nature") or ""
            _dec_tier_raw = dec.get("tier") or ""
            _tier_nature_map = {"short": "intraday", "mid": "swing", "long": "position"}
            _nature_tier_map = {"scalp": "short", "intraday": "short", "swing": "mid", "position": "long", "trend_follow": "long"}
            _dec_trade_nature = normalize_nature(_dec_nature_raw) if _dec_nature_raw else "swing"
            _nature_derived_tier = _nature_tier_map.get(_dec_trade_nature, "mid")
            _eff_tier = _dec_tier_raw or _nature_derived_tier or "mid"

            # 按 (symbol, tier) 精确匹配策略，回退到 symbol 匹配
            strat = host.ensure_bound_strategy(
                db,
                strat_tier_map.get((sym.upper(), _eff_tier))
                or strat_map.get(sym),
                active_ids=active_ids,
                status=("active", "paused"),
            )
            if not strat:
                continue

            trading_mode = session.trading_mode or "paper"
            side = "buy" if operation == "buy" else "sell"
            price = prices.get(sym, 0)

            # 从编排器推断 market_regime
            _orch = orch_directions.get(sym, {})
            _regime = "unknown"
            _mid_bias = _orch.get("mid_bias", "neutral")
            if _mid_bias in ("bearish", "strongly_bearish"):
                _regime = "bearish"
            elif _mid_bias in ("bullish", "strongly_bullish"):
                _regime = "bullish"
            elif _mid_bias == "neutral":
                _regime = "ranging"

            _mkt_dec = (market_data or {}).get(sym, {}) if isinstance(market_data, dict) else {}
            leverage, _lev_src = host.resolve_decision_leverage(
                dec, sym, _eff_tier, _mkt_dec, db, _trading_acct_id,
                trade_nature=_dec_trade_nature, market_summary=market_data,
            )

            # trade_nature 已在上方解析，确保最终值有效
            _strat_tier_raw = getattr(strat, "timeframe_tier", "") or ""
            if not _dec_nature_raw:
                _fallback_nature = _tier_nature_map.get(_eff_tier.strip().lower(), "swing")
                _dec_trade_nature = _fallback_nature

            from backend.services.position_sizing_agent import (
                PositionSizingInput,
                position_sizing_agent,
            )
            _available_for_sizing = float(
                (bal_info or {}).get("available_balance", 0)
                or (bal_info or {}).get("available", 0)
                or (bal_info or {}).get("total_equity", 0)
                or 0
            )
            _lev_cap = dec.get("leverage_cap")
            try:
                _lev_cap = int(_lev_cap) if _lev_cap is not None else None
            except (TypeError, ValueError):
                _lev_cap = None
            _sizing_plan = position_sizing_agent.build_plan(
                PositionSizingInput(
                    symbol=sym,
                    side=side,
                    price=float(price),
                    confidence=float(confidence),
                    total_equity=float((bal_info or {}).get("total_equity", 0) or 0),
                    available_balance=_available_for_sizing,
                    requested_leverage=float(leverage or 0),
                    requested_position_pct=host.extract_ai_position_pct(dec),
                    stop_loss_price=float(dec.get("stop_loss_price", 0) or 0),
                    take_profit_price=float(dec.get("take_profit_price", 0) or 0),
                    volatility_pct=float(_mkt_dec.get("volatility_value", 0.015) or 0.015),
                    tier=_eff_tier,
                    trade_nature=_dec_trade_nature,
                    market_regime=_regime,
                    risk_level="medium",
                    size_multiplier=float(dec.get("size_multiplier") or 1.0),
                    leverage_cap=_lev_cap,
                    alignment_scale=host.resolve_alignment_scale(sym),
                )
            )

            decision_data = {
                "action": operation,
                "side": side,
                "price": price,
                "leverage": _sizing_plan.leverage,
                "position_pct": _sizing_plan.position_pct,
                "confidence_pct": confidence,
                "stop_loss_price": dec.get("stop_loss_price", 0),
                "take_profit_price": dec.get("take_profit_price", 0),
                "_decision_log_id": _decision_log_ids.get(f"{sym}:{_eff_tier}") or _decision_log_ids.get(sym),
                "market_regime": _regime,
                "timeframe_tier": _eff_tier,
                "trade_nature": _dec_trade_nature,
                "_leverage_source": _lev_src,
            }
            decision_data.update(_sizing_plan.to_decision_fields())

            if trading_mode == "paper":
                host.execute_paper_trade(db, session, strat, decision_data)
            else:
                host.execute_live_trade(db, session, strat, dec)

    except Exception as e:
        logger.error(f"[FullAuto] AI 决策流程异常: {e}", exc_info=True)
        host.append_event(session, "ai_error", f"AI决策异常: {str(e)[:100]}")
