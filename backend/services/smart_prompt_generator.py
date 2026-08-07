"""
Smart Prompt Generator Service

Generates intelligent AI trading prompts based on market analysis,
regime detection, and signal configurations.
"""

import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from backend.database.models import Account, PromptTemplate, SignalPool
from backend.services.market_regime_service import (
    get_market_regime,
    get_adaptive_trading_parameters,
    get_multi_timeframe_regime_consensus,
    get_regime_description,
    AdaptiveParameters,
    REGIME_BREAKOUT, REGIME_CONTINUATION, REGIME_ABSORPTION,
    REGIME_EXHAUSTION, REGIME_TRAP, REGIME_NOISE
)
from backend.services.market_data_analyzer import market_data_analyzer
from backend.services.pattern_recognition_service import pattern_recognition_service

logger = logging.getLogger(__name__)


@dataclass
class GeneratedPromptTemplate:
    """Generated prompt template with market context"""
    template_text: str
    template_name: str
    description: str
    
    # Market context
    market_regime_section: str
    adaptive_rules_section: str
    active_patterns_section: str
    
    # Metadata
    symbols: List[str]
    strategy_style: str
    generated_at: str


# Strategy style templates
STRATEGY_STYLE_TEMPLATES = {
    "adaptive": """=== 市场状态自适应交易规则 ===

{market_regime_section}

{adaptive_rules_section}

{active_patterns_section}

=== 交易执行要点 ===
- 根据上述市场状态自动调整交易参数
- 优先关注与当前状态匹配的交易机会
- 在噪音状态下减少交易，等待更清晰的信号
""",

    "trend_following": """=== 趋势跟踪策略规则 ===

{market_regime_section}

**趋势判断标准：**
- EMA20 > EMA50 > EMA100 = 多头趋势
- EMA20 < EMA50 < EMA100 = 空头趋势
- 价格在EMA20上方 = 短期强势
- 价格在EMA20下方 = 短期弱势

**入场规则：**
- 多头：等待价格回调至EMA20附近时买入
- 空头：等待价格反弹至EMA20附近时卖出
- 必须有CVD方向确认

**出场规则：**
- 趋势反转（EMA交叉）时平仓
- 达到ATR止盈目标时部分获利
- 跟踪止损锁定利润

{adaptive_rules_section}
""",

    "mean_reversion": """=== 均值回归策略规则 ===

{market_regime_section}

**超买超卖判断：**
- RSI < 30 = 超卖，考虑做多
- RSI > 70 = 超买，考虑做空
- 布林带%B < 0.1 = 价格接近下轨
- 布林带%B > 0.9 = 价格接近上轨

**入场条件（需同时满足）：**
- RSI极值（<30或>70）
- 价格触及布林带外轨
- CVD出现背离信号

**出场规则：**
- RSI回归50附近
- 价格回到布林带中轨
- 固定止盈止损

{adaptive_rules_section}

{active_patterns_section}
""",

    "breakout": """=== 突破策略规则 ===

{market_regime_section}

**突破确认条件：**
- 价格突破布林带上轨（做多）或下轨（做空）
- OI增加 > 0.5%（新仓位进入）
- CVD方向与突破方向一致
- 成交量放大

**假突破过滤：**
- 必须收盘在突破位之上/下
- OI不能减少（否则为假突破）
- RSI不能处于极端位置

**入场与出场：**
- 突破确认后立即入场
- 止损设在突破前的支撑/阻力位
- 止盈目标为突破幅度的1.5-2倍

{adaptive_rules_section}
""",

    "scalping": """=== 短线交易策略规则 ===

{market_regime_section}

**入场信号：**
- 订单簿不平衡 > 0.6（买盘主导做多）
- 订单簿不平衡 < -0.6（卖盘主导做空）
- Taker比率确认
- 价格接近关键支撑/阻力

**仓位管理：**
- 单笔仓位不超过可用余额的10%
- 快进快出，目标收益0.3-0.5%
- 严格止损，最大亏损0.2%

**风险控制：**
- 每日最多交易次数限制
- 亏损达到日限额后停止交易
- 高波动期间暂停交易

{adaptive_rules_section}
"""
}


class SmartPromptGenerator:
    """
    智能提示词生成器
    
    Generates market-aware AI trading prompts based on:
    1. Current market regime
    2. Historical pattern analysis
    3. Signal pool configuration
    4. Adaptive trading parameters
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def generate_market_aware_prompt(
        self,
        db: Session,
        account_id: int,
        symbols: List[str],
        strategy_style: str = "adaptive",
        time_window: str = "5m",
        tier: str = "mid",
    ) -> GeneratedPromptTemplate:
        """
        Generate a market-aware prompt template.
        
        Args:
            db: Database session
            account_id: Account ID for context
            symbols: List of trading symbols
            strategy_style: adaptive/trend_following/mean_reversion/breakout/scalping
            time_window: Analysis time window
            
        Returns:
            GeneratedPromptTemplate with complete prompt sections
        """
        logger.info(f"Generating market-aware prompt for {symbols} with style {strategy_style}")
        
        # Get account info
        account = db.query(Account).filter(Account.id == account_id).first()
        account_name = account.name if account else "AI Trader"
        
        # Generate market regime section
        market_regime_section = self._generate_market_regime_section(
            db, symbols, time_window
        )
        
        # Generate adaptive rules section
        adaptive_rules_section = self._generate_adaptive_rules_section(
            db, symbols[0] if symbols else "BTC", time_window, tier=tier
        )
        
        # Generate active patterns section
        active_patterns_section = self._generate_active_patterns_section(
            db, symbols, time_window
        )
        
        # Get strategy template
        style_template = STRATEGY_STYLE_TEMPLATES.get(
            strategy_style, 
            STRATEGY_STYLE_TEMPLATES["adaptive"]
        )
        
        # Build the prompt section
        strategy_section = style_template.format(
            market_regime_section=market_regime_section,
            adaptive_rules_section=adaptive_rules_section,
            active_patterns_section=active_patterns_section
        )
        
        # Build complete template
        template_text = self._build_complete_template(
            account_name, symbols, strategy_section, strategy_style
        )
        
        template_name = f"Smart_{strategy_style}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}"
        
        return GeneratedPromptTemplate(
            template_text=template_text,
            template_name=template_name,
            description=f"基于市场分析自动生成的{strategy_style}策略提示词",
            market_regime_section=market_regime_section,
            adaptive_rules_section=adaptive_rules_section,
            active_patterns_section=active_patterns_section,
            symbols=symbols,
            strategy_style=strategy_style,
            generated_at=datetime.now(timezone.utc).isoformat()
        )
    
    def create_signal_linked_prompt(
        self,
        db: Session,
        signal_pool_id: int,
        account_id: int,
        base_template_id: Optional[int] = None
    ) -> GeneratedPromptTemplate:
        """
        Create a prompt linked to a signal pool.
        
        When the signal triggers, the prompt includes:
        - Signal trigger context
        - Historical win rate
        - Recommended position sizing
        - Specific entry/exit rules
        
        Args:
            db: Database session
            signal_pool_id: Signal pool to link
            account_id: Account ID
            base_template_id: Optional base template to extend
            
        Returns:
            GeneratedPromptTemplate configured for signal-triggered trading
        """
        logger.info(f"Creating signal-linked prompt for pool {signal_pool_id}")
        
        # Get signal pool
        pool = db.query(SignalPool).filter(SignalPool.id == signal_pool_id).first()
        if not pool:
            raise ValueError(f"Signal pool {signal_pool_id} not found")
        
        # Get pool symbols
        symbols = pool.symbols if pool.symbols else ["BTC"]
        if isinstance(symbols, str):
            import json
            symbols = json.loads(symbols)
        
        # Generate signal context section
        signal_context_section = self._generate_signal_context_section(db, pool)
        
        # Generate market sections for pool symbols
        market_regime_section = self._generate_market_regime_section(
            db, symbols, "5m"
        )
        
        adaptive_rules_section = self._generate_adaptive_rules_section(
            db, symbols[0], "5m"
        )
        
        # Build template
        template_text = self._build_signal_linked_template(
            pool, signal_context_section, market_regime_section, adaptive_rules_section
        )
        
        template_name = f"SignalLinked_{pool.pool_name}_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        
        return GeneratedPromptTemplate(
            template_text=template_text,
            template_name=template_name,
            description=f"与信号池 '{pool.pool_name}' 关联的智能提示词",
            market_regime_section=market_regime_section,
            adaptive_rules_section=adaptive_rules_section,
            active_patterns_section=signal_context_section,
            symbols=symbols,
            strategy_style="signal_linked",
            generated_at=datetime.now(timezone.utc).isoformat()
        )
    
    def generate_adaptive_strategy_rules(
        self,
        db: Session,
        symbol: str,
        time_window: str = "1h",
        tier: str = "mid",
    ) -> str:
        """
        Generate adaptive strategy rules section for a symbol.
        
        This can be used to enhance existing prompts with
        market-aware trading rules.
        """
        # Get market regime
        regime_data = get_market_regime(db, symbol, time_window)
        regime_type = regime_data.get("regime", REGIME_NOISE)
        direction = regime_data.get("direction", "neutral")
        confidence = regime_data.get("confidence", 0.5)
        
        # Get adaptive parameters
        adaptive_params = get_adaptive_trading_parameters(db, symbol, time_window)
        
        # Build rules
        rules = []
        rules.append(f"=== {symbol} 自适应交易规则 ===\n")
        
        # Regime-specific rules
        regime_rules = self._get_regime_specific_rules(regime_type, direction, tier=tier)
        rules.append(regime_rules)
        
        # Parameter recommendations
        rules.append("\n**交易参数建议：**")
        rules.append(f"- 建议仓位系数：{adaptive_params.position_size_modifier}")
        rules.append(f"- 止损ATR倍数：{adaptive_params.stop_loss_atr_multiple}")
        rules.append(f"- 止盈比例：{adaptive_params.take_profit_ratio}")
        rules.append(f"- 入场确认数：至少{adaptive_params.entry_confirmation_count}个指标确认")
        rules.append(f"- 风险等级：{adaptive_params.risk_level}")
        
        # Direction suggestion
        if adaptive_params.suggested_direction != "neutral":
            rules.append(f"\n**建议方向：{adaptive_params.suggested_direction.upper()}**")
        
        rules.append(f"\n{adaptive_params.notes}")
        
        return "\n".join(rules)
    
    def get_market_context_for_prompt(
        self,
        db: Session,
        symbols: List[str],
        time_window: str = "5m"
    ) -> Dict[str, Any]:
        """
        Get market context data for prompt injection.
        
        This provides structured data that can be used
        with template variables.
        """
        context = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbols": symbols,
            "regimes": {},
            "adaptive_params": {},
            "active_patterns": []
        }
        
        for symbol in symbols:
            # Get regime
            regime_data = get_market_regime(db, symbol, time_window)
            context["regimes"][symbol] = {
                "type": regime_data.get("regime", "noise"),
                "direction": regime_data.get("direction", "neutral"),
                "confidence": regime_data.get("confidence", 0.5),
                "description": get_regime_description(
                    regime_data.get("regime", "noise"),
                    regime_data.get("direction", "neutral")
                )
            }
            
            # Get adaptive params
            params = get_adaptive_trading_parameters(db, symbol, time_window)
            context["adaptive_params"][symbol] = asdict(params)
            
            # Get active patterns
            try:
                patterns = pattern_recognition_service.detect_current_patterns(
                    db, symbol, time_window
                )
                for p in patterns[:3]:
                    context["active_patterns"].append({
                        "symbol": symbol,
                        "name": p.pattern_name,
                        "direction": p.direction,
                        "confidence": p.confidence
                    })
            except Exception as e:
                logger.warning(f"Failed to detect patterns for {symbol}: {e}")
        
        return context
    
    # ========================================================================
    # Private Helper Methods
    # ========================================================================
    
    def _generate_market_regime_section(
        self,
        db: Session,
        symbols: List[str],
        time_window: str
    ) -> str:
        """Generate market regime section for multiple symbols"""
        lines = ["**当前市场状态：**\n"]
        
        for symbol in symbols:
            regime_data = get_market_regime(db, symbol, time_window)
            regime_type = regime_data.get("regime", REGIME_NOISE)
            direction = regime_data.get("direction", "neutral")
            confidence = regime_data.get("confidence", 0.5)
            
            description = get_regime_description(regime_type, direction)
            confidence_pct = int(confidence * 100)
            
            lines.append(f"- **{symbol}**: {description}")
            lines.append(f"  - 信心度: {confidence_pct}%")
            lines.append(f"  - 指标: {regime_data.get('indicators', {})}")
        
        # Add multi-timeframe consensus for primary symbol
        if symbols:
            try:
                consensus = get_multi_timeframe_regime_consensus(db, symbols[0])
                lines.append(f"\n**多周期共识 ({symbols[0]}):**")
                lines.append(f"- 对齐度: {consensus['alignment_score'] * 100:.0f}%")
                lines.append(f"- 共识方向: {consensus['consensus_direction']}")
                lines.append(f"- 建议: {consensus['recommendation']}")
            except Exception as e:
                logger.warning(f"Failed to get MTF consensus: {e}")
        
        return "\n".join(lines)
    
    def _generate_adaptive_rules_section(
        self,
        db: Session,
        symbol: str,
        time_window: str,
        tier: str = "mid",
    ) -> str:
        """Generate adaptive trading rules section with tier awareness"""
        params = get_adaptive_trading_parameters(db, symbol, time_window)

        lines = ["\n**自适应交易参数：**\n"]

        # ── Tier context injection ──
        try:
            from backend.config.settings import TIER_PROTECTION_PARAMS, TIER_TP_SL_DEFAULTS
            _tier_cfg = TIER_PROTECTION_PARAMS.get(tier, TIER_PROTECTION_PARAMS["mid"])
            _tier_tp_sl = TIER_TP_SL_DEFAULTS.get(tier, TIER_TP_SL_DEFAULTS["mid"])

            _tier_names = {"short": "短期/日内", "mid": "中期/波段", "long": "长期/趋势"}
            lines.append(f"\n**[交易周期: {_tier_names.get(tier, tier)} ({tier.upper()})]**")
            lines.append(f"- 最短持仓: {_tier_cfg['min_hold_sec'] // 60} 分钟")
            lines.append(f"- 锁利阶段: {_tier_cfg['lock_stages']} 次")
            lines.append(f"- 默认 TP: {_tier_tp_sl['tp_pct']*100:.1f}% / SL: {_tier_tp_sl['sl_pct']*100:.1f}%")
            lines.append(f"- 冷却期: {_tier_cfg['cooldown_sec'] // 60} 分钟")
            lines.append("")
        except Exception:
            pass

        lines.append(f"| 参数 | 建议值 |")
        lines.append(f"|------|--------|")
        lines.append(f"| 仓位系数 | {params.position_size_modifier}x |")
        lines.append(f"| 止损ATR倍数 | {params.stop_loss_atr_multiple} |")
        lines.append(f"| 止盈/止损比 | {params.take_profit_ratio} |")
        lines.append(f"| 入场确认数 | {params.entry_confirmation_count} |")
        lines.append(f"| 推荐策略 | {params.recommended_strategy} |")
        
        if params.suggested_direction != "neutral":
            lines.append(f"\n**倾向方向: {params.suggested_direction.upper()}**")
        
        lines.append(f"\n> {params.notes}")
        
        return "\n".join(lines)
    
    def _generate_active_patterns_section(
        self,
        db: Session,
        symbols: List[str],
        time_window: str
    ) -> str:
        """Generate section about currently active patterns"""
        lines = ["\n**当前激活的交易模式：**\n"]
        
        found_patterns = False
        for symbol in symbols:
            try:
                patterns = pattern_recognition_service.detect_current_patterns(
                    db, symbol, time_window
                )
                if patterns:
                    found_patterns = True
                    for p in patterns[:3]:  # Top 3 per symbol
                        win_rate = f"{p.historical_win_rate*100:.1f}%" if p.historical_win_rate else "N/A"
                        lines.append(f"- **{p.pattern_name}** ({symbol})")
                        lines.append(f"  - 方向: {p.direction}")
                        lines.append(f"  - 信心度: {p.confidence:.0%}")
                        lines.append(f"  - 历史胜率: {win_rate}")
            except Exception as e:
                logger.warning(f"Failed to detect patterns for {symbol}: {e}")
        
        if not found_patterns:
            lines.append("当前无明确的交易模式激活。建议等待更清晰的信号。")
        
        return "\n".join(lines)
    
    def _generate_signal_context_section(
        self,
        db: Session,
        pool: SignalPool
    ) -> str:
        """Generate signal context section for signal-linked prompts"""
        import json
        
        lines = ["\n**信号池配置：**\n"]
        lines.append(f"- 名称: {pool.pool_name}")
        lines.append(f"- 逻辑: {pool.logic or 'AND'}")
        
        # Parse signal IDs
        signal_ids = pool.signal_ids
        if isinstance(signal_ids, str):
            signal_ids = json.loads(signal_ids)
        
        lines.append(f"- 包含信号数: {len(signal_ids) if signal_ids else 0}")
        
        lines.append("\n**当信号触发时的建议行为：**")
        lines.append("1. 验证触发信号与当前市场状态的一致性")
        lines.append("2. 检查多个信号是否协同触发")
        lines.append("3. 根据自适应参数调整仓位大小")
        lines.append("4. 设置合理的止损止盈")
        
        return "\n".join(lines)
    
    def _build_complete_template(
        self,
        account_name: str,
        symbols: List[str],
        strategy_section: str,
        strategy_style: str
    ) -> str:
        """Build complete prompt template"""
        symbols_str = ", ".join(symbols)
        
        template = f"""你是 {account_name}，一个在 Hyper Alpha Arena 平台上运作的专业加密货币交易AI。

=== 交易环境 ===
{{trading_environment}}

=== 账户状态 ===
可用资金: ${{available_cash}}
账户总价值: ${{total_account_value}}

=== 监控交易对 ===
{symbols_str}

=== 市场价格 ===
{{market_prices}}

{strategy_section}

=== 触发上下文 ===
{{trigger_context}}

**重要提示**：触发不是交易指令。它提示你使用完整的策略规则重新评估市场。即使触发有效，如果其他条件不满足，结果仍可能是 "hold"。

=== 风险管理 ===
- 单币种最大仓位: 可用余额的30%
- 保持总保证金使用率低于70%
- 设置明确的止盈止损
- 亏损超过日限额时停止交易

=== 决策要求 ===
- operation: "buy"（做多）、"sell"（做空）、"hold" 或 "close"
- target_portion_of_balance: 0.0-1.0
- leverage: 5 到 {{max_leverage}}（根据置信度和波动率动态调整，充分利用 5-20x 全范围）
- max_price: "buy" 时必填
- min_price: "sell"/"close" 时必填
- take_profit_price: 止盈价格（必填）
- stop_loss_price: 止损价格（必填）

=== 输出格式 ===
{{output_format}}
"""
        return template
    
    def _build_signal_linked_template(
        self,
        pool: SignalPool,
        signal_context: str,
        market_regime_section: str,
        adaptive_rules_section: str
    ) -> str:
        """Build signal-linked prompt template"""
        template = f"""你是一个在 Hyper Alpha Arena 平台上运作的信号驱动型交易AI。

=== 交易环境 ===
{{trading_environment}}

=== 账户状态 ===
可用资金: ${{available_cash}}
账户总价值: ${{total_account_value}}

=== 关联信号池: {pool.pool_name} ===
{signal_context}

=== 市场分析 ===
{market_regime_section}

{adaptive_rules_section}

=== 触发上下文 ===
{{trigger_context}}

**信号触发处理流程：**
1. 首先确认触发的具体信号和条件
2. 验证当前市场状态是否支持该信号方向
3. 根据自适应参数计算适当的仓位大小
4. 设置止盈止损后执行交易

=== 决策要求 ===
- operation: "buy"（做多）、"sell"（做空）、"hold" 或 "close"
- target_portion_of_balance: 0.0-1.0
- leverage: 5 到 {{max_leverage}}（根据置信度动态调整，充分利用 5-20x 全范围）
- 必须设置止盈止损

=== 输出格式 ===
{{output_format}}
"""
        return template
    
    def _get_regime_specific_rules(self, regime_type: str, direction: str, tier: str = "mid") -> str:
        """Get regime-specific trading rules with tier awareness"""
        rules = {
            REGIME_BREAKOUT: """
**突破状态交易规则：**
- 顺应突破方向入场
- 等待价格回调至支撑/阻力位
- 使用标准仓位（20%可用余额）
- 止损：突破失败（价格回到突破位以下）
- 止盈：趋势延伸目标（+15%到+20%）
""",
            REGIME_CONTINUATION: """
**趋势延续交易规则：**
- 顺势入场，等待回调
- 多头：MA5>MA20>MA60，价格回调至MA10附近
- 空头：MA5<MA20<MA60，价格反弹至MA10附近
- 使用正常仓位（20%可用余额）
- 止损：趋势反转时（-8%）
- 止盈：趋势目标（+12%到+18%）
""",
            REGIME_ABSORPTION: """
**吸收状态（震荡市）交易规则：**
- 在支撑位做多，在阻力位做空
- 支撑位：价格接近10周期低点 + RSI<35
- 阻力位：价格接近10周期高点 + RSI>65
- 使用减半仓位（10%可用余额）
- 止损：更紧（-3%）
- 止盈：收窄目标（+3%到+5%）
""",
            REGIME_EXHAUSTION: """
**衰竭状态交易规则：**
- 考虑反向操作
- 等待RSI极端且CVD背离时入场
- 使用正常仓位（20%可用余额）
- 止损：反向判断失败（-6%）
- 止盈：回归目标（+10%）
""",
            REGIME_TRAP: """
**陷阱状态交易规则：**
- 警惕虚假突破
- 假突破后，价格回到区间内时反向入场
- 使用谨慎仓位（15%可用余额）
- 止损：严格（-4%）
- 止盈：保守目标（+6%到+8%）
""",
            REGIME_NOISE: """
**噪音状态交易规则：**
- 主策略：观望（hold）
- 仅在极端超买超卖时考虑交易
- RSI<20或RSI>80，且出现成交量激增
- 如必须交易：极小仓位（5%可用余额）
- 止损：紧（-2%）
"""
        }

        base_rules = rules.get(regime_type, rules[REGIME_NOISE])

        # ── Tier-specific overlay ──
        _tier_overlays = {
            "short": f"""

**[SHORT-TIER CYCLE OVERRIDE]**
- Expected hold: 2-8 hours — this is a quick trade, NO patience for drawdowns
- ATR stop multiplier: 1.0-1.5x (TIGHT stops)
- Risk-Reward minimum: 1.5:1
- Use 5m/15m timeframe for entry/exit timing
- If breakout stalls or volume dries up → EXIT IMMEDIATELY, do not wait
- Single profit-lock at 70% progress (close 50%), then trail tight to TP
- DO NOT hold through adverse swings — cut fast, re-enter later
""",
            "mid": "",  # Default rules are designed for mid-tier, no override needed
            "long": f"""

**[LONG-TIER CYCLE OVERRIDE]**
- Expected hold: 3-14 days — let profits RUN, tolerate normal volatility
- ATR stop multiplier: 3.0-4.0x (WIDE stops)
- Risk-Reward minimum: 3.0:1
- Use 4h/1d timeframe for confirmation — ignore 15m noise
- Only exit on CONFIRMED trend reversal, not intra-day noise
- Single profit-lock at 85% progress (close only 30%), leave 70% to ride the trend
- Tolerate drawdowns up to 60% from peak profit — DO NOT panic-sell
""",
        }

        overlay = _tier_overlays.get(tier, "")
        return base_rules + overlay if overlay else base_rules


# Singleton instance
smart_prompt_generator = SmartPromptGenerator()
