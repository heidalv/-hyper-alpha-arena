"""
AI Decision Integration - AI决策集成模块

集成因子引擎和自适应执行层到AI决策流程：
1. 因子信息整合
2. 市场状态检测
3. 止盈止损参数
4. 仓位计算

Author: Hyper-Alpha-Arena
"""

import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FactorContext:
    """因子上下文"""
    factor_values: Dict[str, Any]
    adaptive_weights: Dict[str, float]
    market_regime: str
    regime_confidence: float
    selected_factors: List[str]


@dataclass
class ExecutionContext:
    """执行上下文"""
    stop_loss_pct: float
    take_profit_pct: float
    position_size_pct: float
    trailing_stop_enabled: bool
    time_stop_enabled: bool
    recommended_leverage: float
    risk_reward_ratio: float


def build_factor_context(
    symbol: str,
    klines: Any,
    market_data: Optional[Dict] = None
) -> FactorContext:
    """
    构建因子上下文
    
    Args:
        symbol: 交易品种
        klines: K线数据
        market_data: 市场数据
        
    Returns:
        FactorContext对象
    """
    try:
        from services.factor_engine import (
            factor_engine,
            get_factor_weighting,
            MarketRegime
        )
        
        if klines is None or klines.empty:
            return FactorContext(
                factor_values={},
                adaptive_weights={},
                market_regime="unknown",
                regime_confidence=0.0,
                selected_factors=[]
            )
        
        factor_values = factor_engine.compute_all_factors(klines, market_data)
        
        weighting = get_factor_weighting()
        adaptive_result = weighting.calculate_adaptive_weights(factor_values, market_data)
        
        return FactorContext(
            factor_values={k: v.value for k, v in factor_values.items()},
            adaptive_weights=adaptive_result.weights,
            market_regime=adaptive_result.regime.value,
            regime_confidence=adaptive_result.confidence,
            selected_factors=list(adaptive_result.weights.keys())[:10]
        )
        
    except Exception as e:
        logger.warning(f"[AIIntegration] Failed to build factor context for {symbol}: {e}")
        return FactorContext(
            factor_values={},
            adaptive_weights={},
            market_regime="unknown",
            regime_confidence=0.0,
            selected_factors=[]
        )


def _compute_factor_values(symbol: str, klines) -> "Dict[str, Any]":
    """[2026-08-14 P1-E4] 计算原始 FactorValue 对象（供 D7 因子引导链路使用）。

    build_factor_context 会把 factor_values 转成裸 float（供展示/其它消费），
    而 FactorSignalGenerator.generate_signals / DecisionFusionEngine.fuse 需要
    完整的 FactorValue（value/category/is_directional/has_data 字段）。
    """
    try:
        from services.factor_engine import factor_engine
        return factor_engine.compute_all_factors(klines, None)
    except Exception:
        return {}


def build_execution_context(
    symbol: str,
    entry_price: float,
    side: str,
    atr: float,
    market_regime: str,
    confidence: float = 0.8
) -> ExecutionContext:
    """
    构建执行上下文
    
    Args:
        symbol: 交易品种
        entry_price: 入场价格
        side: 'long' 或 'short'
        atr: ATR值
        market_regime: 市场状态
        confidence: 置信度
        
    Returns:
        ExecutionContext对象
    """
    try:
        from services.adaptive_executor import (
            get_stop_manager,
            get_position_sizer
        )
        from services.factor_engine import MarketRegime
        
        stop_manager = get_stop_manager()
        sizer = get_position_sizer()
        
        initial_stop = stop_manager.calculate_initial_stop(entry_price, atr, side)
        
        tp_levels = stop_manager.calculate_take_profit_levels(entry_price, atr, side)
        tp1_price = tp_levels.get("LEVEL_1", (entry_price, 0.33))[0]
        
        if entry_price > 0:
            rr_ratio = abs(tp1_price - entry_price) / abs(initial_stop.price - entry_price)
        else:
            rr_ratio = 2.0
        
        # 将字符串市场状态转换为枚举
        try:
            regime_enum = MarketRegime(market_regime) if isinstance(market_regime, str) else market_regime
        except ValueError:
            regime_enum = MarketRegime.NOISE
        
        regime_params = stop_manager.get_trading_parameters(regime_enum)
        
        position_result = sizer.calculate_position_size(
            entry_price=entry_price,
            stop_loss=initial_stop.price,
            side=side,
            confidence=confidence,
            current_volatility=atr / entry_price if entry_price > 0 else 0.5
        )
        
        return ExecutionContext(
            stop_loss_pct=initial_stop.distance_pct,
            take_profit_pct=regime_params.get('take_profit_atr_multiple', 2.0) * (atr / entry_price if entry_price > 0 else 0.02),
            position_size_pct=position_result.size_pct,
            trailing_stop_enabled=True,
            time_stop_enabled=True,
            recommended_leverage=position_result.leverage,
            risk_reward_ratio=rr_ratio
        )
        
    except Exception as e:
        logger.warning(f"[AIIntegration] Failed to build execution context for {symbol}: {e}")
        return ExecutionContext(
            stop_loss_pct=0.02,
            take_profit_pct=0.04,
            position_size_pct=0.1,
            trailing_stop_enabled=True,
            time_stop_enabled=True,
            recommended_leverage=10.0,
            risk_reward_ratio=2.0
        )


def format_factor_summary(factor_context: FactorContext) -> str:
    """格式化因子摘要"""
    if not factor_context.factor_values:
        return "因子数据不可用"
    
    lines = [f"市场状态: {factor_context.market_regime} (置信度: {factor_context.regime_confidence:.2%})"]
    lines.append("")
    lines.append("因子权重 (Top 5):")
    
    sorted_factors = sorted(
        factor_context.adaptive_weights.items(),
        key=lambda x: x[1],
        reverse=True
    )[:5]
    
    for factor, weight in sorted_factors:
        value = factor_context.factor_values.get(factor, "N/A")
        value_str = f"{value:.4f}" if isinstance(value, float) else str(value)
        lines.append(f"  - {factor}: {value_str} (权重: {weight:.2%})")
    
    return "\n".join(lines)


def format_execution_summary(exec_context: ExecutionContext) -> str:
    """格式化执行参数摘要"""
    lines = [
        f"建议仓位: {exec_context.position_size_pct:.1%}",
        f"建议杠杆: {exec_context.recommended_leverage:.0f}x",
        f"止损距离: {exec_context.stop_loss_pct:.2%}",
        f"止盈距离: {exec_context.take_profit_pct:.2%}",
        f"风险回报比: {exec_context.risk_reward_ratio:.2f}",
        f"移动止损: {'启用' if exec_context.trailing_stop_enabled else '禁用'}",
        f"时间止损: {'启用' if exec_context.time_stop_enabled else '禁用'}"
    ]
    return "\n".join(lines)


def get_adaptive_parameters_for_symbol(
    symbol: str,
    klines: Any,
    entry_price: float,
    side: str,
    market_data: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    获取品种的自适应参数
    
    整合因子分析和执行参数，
    为AI决策提供完整的自适应参数
    """
    factor_context = build_factor_context(symbol, klines, market_data)
    
    atr = 0.0
    if klines is not None and not klines.empty:
        try:
            from services.factor_engine import factor_engine
            atr = factor_engine.compute_atr(klines, market_data)
        except Exception:
            atr = entry_price * 0.02 if entry_price > 0 else 100.0
    
    exec_context = build_execution_context(
        symbol,
        entry_price,
        side,
        atr,
        factor_context.market_regime,
        factor_context.regime_confidence
    )
    
    return {
        'market_regime': factor_context.market_regime,
        'regime_confidence': factor_context.regime_confidence,
        'factor_weights': factor_context.adaptive_weights,
        'factor_summary': format_factor_summary(factor_context),
        'execution_parameters': {
            'position_size_pct': exec_context.position_size_pct,
            'stop_loss_pct': exec_context.stop_loss_pct,
            'take_profit_pct': exec_context.take_profit_pct,
            'trailing_stop': exec_context.trailing_stop_enabled,
            'time_stop': exec_context.time_stop_enabled,
            'leverage': exec_context.recommended_leverage,
            'risk_reward_ratio': exec_context.risk_reward_ratio
        },
        'execution_summary': format_execution_summary(exec_context)
    }


def add_adaptive_context_to_prompt(
    context: Dict[str, Any],
    prices: Dict[str, float],
    ordered_symbols: List[str],
    klines_data: Dict[str, Any],
    market_data: Optional[Dict[str, Any]] = None,
    positions: Optional[Dict[str, Dict[str, Any]]] = None,
    orchestrator_actions: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    为所有品种添加自适应上下文
    
    Args:
        context: 现有上下文
        prices: 价格数据
        ordered_symbols: 排序的品种列表
        klines_data: K线数据
        market_data: 市场数据
        positions: [V3整合] 当前仓位 {symbol: {"side": "long"/"short"}}
        orchestrator_actions: [V3整合] 编排器动作 {symbol: "frozen"/"hold"/"buy"/"sell"}
        
    Returns:
        更新后的上下文
    """
    adaptive_context = {}
    
    for symbol in ordered_symbols:
        price = prices.get(symbol, 0)
        klines = klines_data.get(symbol)
        
        if price <= 0:
            continue
        
        # [2026-08-14 P2-19 修复] side 此前硬编码 'long'：即使 positions 显示该
        # symbol 为空头/空仓，止损/止盈/仓位方向仍按多头计算。改用实际持仓方向。
        _pos_side = None
        if positions:
            try:
                _pos_side = (positions.get(symbol) or {}).get("side")
            except Exception:
                _pos_side = None
        params = get_adaptive_parameters_for_symbol(
            symbol=symbol,
            klines=klines,
            entry_price=price,
            side=_pos_side or 'long',
            market_data=market_data.get(symbol) if market_data else None
        )
        
        adaptive_context[f"{symbol}_market_regime_new"] = params['market_regime']
        adaptive_context[f"{symbol}_regime_confidence"] = f"{params['regime_confidence']:.2%}"
        adaptive_context[f"{symbol}_factor_weights"] = params['factor_summary']
        adaptive_context[f"{symbol}_execution_params"] = params['execution_summary']
        adaptive_context[f"{symbol}_position_size"] = f"{params['execution_parameters']['position_size_pct']:.1%}"
        adaptive_context[f"{symbol}_risk_reward"] = f"{params['execution_parameters']['risk_reward_ratio']:.2f}"
        
        # V3 整合: 决策融合引擎
        fusion_result = compute_fusion_decision(
            symbol=symbol,
            klines=klines,
            market_data=market_data.get(symbol) if market_data else None,
            position_side=(positions.get(symbol, {}).get("side") if positions else None),
            orchestrator_action=(orchestrator_actions.get(symbol) if orchestrator_actions else None),
        )
        if fusion_result:
            adaptive_context[f"{symbol}_fusion_action"] = fusion_result["action"]
            adaptive_context[f"{symbol}_fusion_confidence"] = f"{fusion_result['confidence']:.2f}"
            adaptive_context[f"{symbol}_fusion_direction"] = f"{fusion_result['signal_direction']:+.2f}"
            adaptive_context[f"{symbol}_fusion_reasoning"] = fusion_result["reasoning"]
    
    adaptive_context['adaptive_trading_summary'] = _build_adaptive_summary(prices, ordered_symbols, klines_data, market_data)
    
    return {**context, **adaptive_context}


def _build_adaptive_summary(
    prices: Dict[str, float],
    ordered_symbols: List[str],
    klines_data: Dict[str, Any],
    market_data: Optional[Dict] = None
) -> str:
    """构建自适应交易摘要"""
    lines = ["=== 自适应交易参数 ==="]
    
    regimes = []
    for symbol in ordered_symbols[:5]:
        klines = klines_data.get(symbol)
        params = get_adaptive_parameters_for_symbol(
            symbol, klines, prices.get(symbol, 0), 'long', market_data
        )
        regimes.append(
            f"- {symbol}: {params['market_regime']} "
            f"(仓位: {params['execution_parameters']['position_size_pct']:.0%}, "
            f"RR: {params['execution_parameters']['risk_reward_ratio']:.1f})"
        )
    
    lines.append("\n".join(regimes))
    
    return "\n".join(lines)


def compute_fusion_decision(
    symbol: str,
    klines: Any,
    market_data: Optional[Dict] = None,
    position_side: Optional[str] = None,
    orchestrator_action: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """V3 整合: 使用 DecisionFusionEngine 计算融合决策。

    将因子信号 + 质量 + 编排器状态 → FusionDecision。
    返回 None 表示融合失败（不影响原有流程）。
    """
    try:
        from services.factor_engine import factor_engine, get_factor_weighting
        from services.factor_engine.decision_fusion_engine import DecisionFusionEngine

        if klines is None or klines.empty:
            return None

        factor_values = factor_engine.compute_all_factors(klines, market_data)
        if not factor_values:
            return None

        weighting = get_factor_weighting()
        adaptive_result = weighting.calculate_adaptive_weights(factor_values, market_data)

        fusion_engine = DecisionFusionEngine()
        decision = fusion_engine.fuse(
            factor_values=factor_values,
            weights=adaptive_result.weights,
            regime=adaptive_result.regime.value,
            orchestrator_action=orchestrator_action,
            position_side=position_side,
        )

        return {
            "action": decision.action,
            "confidence": decision.confidence,
            "signal_direction": decision.signal_direction,
            "signal_strength": decision.signal_strength,
            "data_quality": decision.data_quality,
            "regime": decision.regime,
            "reasoning": decision.reasoning,
        }

    except Exception as e:
        logger.debug(f"[AIIntegration] 融合决策计算失败 {symbol}: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  D7: 量化因子→AI 决策引导（因子引擎 → LLM prompt）
# ══════════════════════════════════════════════════════════════
def build_factor_guidance_for_prompt(
    symbols: List[str],
    klines_data: Dict[str, Any],
    prices: Dict[str, float],
) -> str:
    """为 AI prompt 构建结构化量化因子引导文本。"""
    if not symbols or not klines_data:
        return "（因子引擎: 无可用数据）"

    lines = [
        "═══════════════════════════════",
        "📊 量化因子引导（参考数据，非指令）",
        "═══════════════════════════════",
        "以下因子信号供参考。你应该独立判断方向——若与因子一致请说明你的独立理由，",
        "若与因子矛盾也请说明你的分析依据。",
        "",
    ]

    # P0-3: 注入方向历史胜率警告，从 StrategyRegimeScore 读取
    _dir_warnings = _get_direction_performance_warnings(symbols)

    try:
        from .factor_engine.factor_signal_generator import FactorSignalGenerator
        from .factor_engine.decision_fusion_engine import DecisionFusionEngine
        _sig_gen = FactorSignalGenerator()
        _fusion = DecisionFusionEngine()

        for sym in symbols[:12]:
            klines = klines_data.get(sym)
            price = prices.get(sym, 0)
            if klines is None or klines.empty or price <= 0:
                continue
            try:
                factor_ctx = build_factor_context(sym, klines)
                # [2026-08-14 P1-E4 修复] 此前调用了不存在的 _sig_gen.generate(...)
                # （FactorSignalGenerator 只有 generate_signals）且 fuse 按错误
                # 签名传参，加上 factor_ctx.factor_values 已被转成裸 float →
                # 每个 symbol 都抛异常输出"因子计算失败"，D7 因子引导从未生效。
                # 修复：重新计算 FactorValue 对象 → generate_signals → fuse 按
                # 真实签名传参；adaptive_weights 复用 factor_ctx 已算好的 regime 权重。
                _fv = _compute_factor_values(sym, klines)
                if not _fv:
                    continue
                _regime = str(getattr(factor_ctx, "market_regime", "unknown") or "unknown")
                _weights = dict(getattr(factor_ctx, "adaptive_weights", {}) or {})
                composite = _sig_gen.generate_signals(_fv, weights=_weights, regime=_regime)
                fusion = _fusion.fuse(
                    factor_values=_fv,
                    weights=_weights,
                    regime=_regime,
                )
                _dir_symbol = "🟢" if fusion.signal_direction > 0.2 else ("🔴" if fusion.signal_direction < -0.2 else "⚪")
                lines.append(
                    f"{_dir_symbol} {sym:6s} | 方向={fusion.signal_direction:+.2f} "
                    f"强度={fusion.signal_strength:.2f} | 置信={fusion.confidence:.2f} "
                    f"| 状态={fusion.regime} | 融合建议={fusion.action.upper()}"
                )
                top_factors = sorted(
                    _weights.items(),
                    key=lambda x: x[1], reverse=True
                )[:3]
                if top_factors:
                    _fstr = ", ".join([f"{k}={v:.2f}" for k, v in top_factors])
                    lines.append(f"       关键因子: {_fstr}")
                # P0-3: 注入该 symbol 的方向胜率警告
                if sym in _dir_warnings:
                    lines.append(f"       {_dir_warnings[sym]}")
            except Exception:
                lines.append(f"  ⚠️ {sym}: 因子计算失败")
    except Exception as _g_err:
        logger.warning(f"[FactorGuidance] 失败: {_g_err}")
        return "（因子引擎: 临时错误）"

    lines.append("\n═══════════════════════════════")
    return "\n".join(lines)


def _get_direction_performance_warnings(symbols: List[str]) -> Dict[str, str]:
    """P0-3/P2-1: 从 strategy_trades 查询各 symbol 的方向历史胜率，生成警告文本。"""
    warnings: Dict[str, str] = {}
    try:
        from backend.database.connection import SessionLocal
        from backend.database.dialect import dialect
        from sqlalchemy import text
        db = SessionLocal()
        try:
            for sym in symbols:
                rows = db.execute(text("""
                    SELECT side,
                           COUNT(*) as cnt,
                           SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                    FROM strategy_trades
                    WHERE symbol = :sym
                      AND created_at >= """ + dialect.datetime_now_minus(30) + """
                    GROUP BY side
                """), {"sym": sym}).fetchall()
                _warns = []
                for side, cnt, wins in rows:
                    if cnt and int(cnt) >= 10:
                        wr = float(wins) / float(cnt)
                        if wr < 0.35:
                            _dir_cn = "做多" if (side or "").lower() in ("long", "buy") else "做空"
                            _warns.append(f"{_dir_cn}胜率{wr*100:.0f}%({int(cnt)}笔)")
                if _warns:
                    warnings[sym] = f"⚠️ 历史表现: {', '.join(_warns)} — 建议 HOLD 或反向"
        finally:
            db.close()
    except Exception as _e:
        logger.debug(f"[FactorGuidance] 方向胜率查询失败(非致命): {_e}")
    return warnings
