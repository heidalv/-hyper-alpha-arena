"""
Short Term Tactician - 短期战术器

负责日内交易的战术决策：
1. 入场时机选择
2. 短期趋势判断
3. 动态止盈止损调整
4. 快速反应机制

Author: Hyper-Alpha-Arena
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class TacticalAction(Enum):
    """战术动作"""
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT_LONG = "exit_long"
    EXIT_SHORT = "exit_short"
    EXIT = "exit"              # 通用出场（由持仓方向自行决定）
    ADD_LONG = "add_long"
    ADD_SHORT = "add_short"
    REDUCE_LONG = "reduce_long"
    REDUCE_SHORT = "reduce_short"
    HOLD = "hold"
    WAIT = "wait"


class EntryTiming(Enum):
    """入场时机"""
    AGGRESSIVE = "aggressive"       # 激进入场
    STANDARD = "standard"           # 标准入场
    CONSERVATIVE = "conservative"   # 保守入场
    PULLBACK = "pullback"           # 回调入场


class MarketCondition(Enum):
    """市场状况"""
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    VOLATILE = "volatile"
    QUIET = "quiet"
    REVERSAL = "reversal"


@dataclass
class TacticalSignal:
    """战术信号"""
    symbol: str
    action: TacticalAction
    confidence: float
    entry_timing: EntryTiming
    suggested_price: float
    stop_loss: float
    take_profit: float
    position_size_pct: float
    reasons: List[str]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    valid_until: Optional[datetime] = None


@dataclass
class ShortTermContext:
    """短期交易上下文"""
    symbol: str
    current_price: float
    
    # 价格数据
    vwap: float = 0.0
    ema_9: float = 0.0
    ema_21: float = 0.0
    
    # 技术指标
    rsi: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    
    # 波动率
    atr: float = 0.0
    atr_pct: float = 0.0
    
    # 成交量
    volume_ratio: float = 1.0  # 相对平均成交量
    
    # 订单流
    taker_buy_ratio: float = 0.5
    cvd_delta: float = 0.0
    
    # 市场状况
    market_condition: MarketCondition = MarketCondition.QUIET


@dataclass 
class TacticalConfig:
    """战术配置"""
    # 入场配置
    min_confidence: float = 0.6
    max_position_size: float = 0.3
    
    # 来自长期规划器的硬约束（由编排层注入）
    # 统一枚举: "long_only" / "short_only" / "both"（兼容旧值 "long"/"short"/"any"）
    allowed_direction: str = "both"
    long_term_bias: str = "neutral"      # "long" / "short" / "neutral"
    long_term_max_position: float = 0.30 # 长线风险预算对短线的仓位上限
    
    # RSI 阈值
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    rsi_extreme_high: float = 80.0
    rsi_extreme_low: float = 20.0
    
    # ATR 乘数
    sl_atr_multiple: float = 1.5
    tp_atr_multiple: float = 2.5
    
    # V5.1 安全钳位：短线 tier 硬上限（防高波动时 SL/TP 过度膨胀）
    max_sl_pct: float = 0.04    # SL 距离硬上限 4%（对标 coordinator short cap）
    min_rr_ratio: float = 1.2   # 最低盈亏比 1.2:1
    
    # 成交量阈值
    volume_surge_threshold: float = 2.0
    
    # 时间配置
    signal_valid_minutes: int = 15
    min_holding_minutes: int = 5


class ShortTermTactician:
    """
    短期战术器
    
    根据实时市场数据做出日内交易的战术决策
    """
    
    def __init__(self, config: Optional[TacticalConfig] = None):
        self.config = config or TacticalConfig()
        
        # 活跃信号缓存
        self._active_signals: Dict[str, TacticalSignal] = {}
        
        # 最近决策历史
        self._decision_history: Dict[str, List[TacticalSignal]] = {}
        
        logger.info("[ShortTermTactician] Initialized")
    
    def analyze(self, context: ShortTermContext) -> TacticalSignal:
        """
        分析短期交易机会
        
        Args:
            context: 短期交易上下文
            
        Returns:
            TacticalSignal 战术信号
        """
        symbol = context.symbol
        
        # 最短持有期检查：开仓后 min_holding_minutes 内禁止反向信号
        _last_entry_signal = self._active_signals.get(symbol)
        if (_last_entry_signal
                and _last_entry_signal.action in (
                    TacticalAction.ENTER_LONG, TacticalAction.ENTER_SHORT)
                and _last_entry_signal.timestamp):
            from datetime import datetime, timedelta
            _hold_deadline = _last_entry_signal.timestamp + timedelta(
                minutes=self.config.min_holding_minutes)
            if datetime.now(timezone.utc) < _hold_deadline:
                _remain = (_hold_deadline - datetime.now(timezone.utc)).total_seconds() / 60
                logger.debug(
                    f"[Tactician] {symbol} 最短持有期内(剩{_remain:.0f}min)，维持当前方向")
                return TacticalSignal(
                    action=TacticalAction.HOLD,
                    symbol=symbol,
                    confidence=0.3,
                    reasons=[f"最短持有期{self.config.min_holding_minutes}min内，维持观望"],
                    timestamp=datetime.now(timezone.utc),
                )
        
        # 确定市场状况
        market_condition = self._assess_market_condition(context)
        context.market_condition = market_condition
        
        # 分析入场机会
        entry_signal = self._analyze_entry(context)
        
        # 分析出场机会（如果有持仓）
        exit_signal = self._analyze_exit(context)
        
        # 综合决策
        final_signal = self._make_decision(context, entry_signal, exit_signal)
        
        # 缓存信号
        self._active_signals[symbol] = final_signal
        
        # 记录历史
        if symbol not in self._decision_history:
            self._decision_history[symbol] = []
        self._decision_history[symbol].append(final_signal)
        
        return final_signal
    
    def _assess_market_condition(self, ctx: ShortTermContext) -> MarketCondition:
        """评估市场状况"""
        # 趋势判断
        trend_score = 0
        
        # EMA 趋势
        if ctx.ema_9 > ctx.ema_21:
            trend_score += 1
        elif ctx.ema_9 < ctx.ema_21:
            trend_score -= 1
        
        # 价格位置
        if ctx.current_price > ctx.vwap:
            trend_score += 0.5
        else:
            trend_score -= 0.5
        
        # MACD 动量
        if ctx.macd_histogram > 0:
            trend_score += 0.5
        else:
            trend_score -= 0.5
        
        # 波动率判断
        is_volatile = ctx.atr_pct > 0.03  # ATR > 3%
        
        # RSI 极值判断
        is_extreme = ctx.rsi > self.config.rsi_extreme_high or ctx.rsi < self.config.rsi_extreme_low
        
        # 综合判断
        if is_extreme and abs(trend_score) > 1:
            return MarketCondition.REVERSAL
        
        if is_volatile:
            return MarketCondition.VOLATILE
        
        if trend_score >= 1.5:
            return MarketCondition.TRENDING_UP
        elif trend_score <= -1.5:
            return MarketCondition.TRENDING_DOWN
        elif abs(trend_score) < 0.5 and not is_volatile:
            return MarketCondition.QUIET
        else:
            return MarketCondition.RANGING
    
    def _analyze_entry(self, ctx: ShortTermContext) -> Optional[TacticalSignal]:
        """分析入场机会"""
        reasons = []
        confidence = 0.0
        action = TacticalAction.WAIT
        entry_timing = EntryTiming.STANDARD
        
        # ========== 多头入场分析 ==========
        long_score = 0.0
        long_reasons = []
        
        # 趋势对齐
        if ctx.ema_9 > ctx.ema_21 and ctx.current_price > ctx.vwap:
            long_score += 0.3
            long_reasons.append("趋势对齐: 9EMA > 21EMA, 价格 > VWAP")
        
        # RSI 超卖反弹
        if ctx.rsi < self.config.rsi_oversold:
            long_score += 0.25
            long_reasons.append(f"RSI超卖: {ctx.rsi:.1f}")
            entry_timing = EntryTiming.AGGRESSIVE
        elif ctx.rsi < 45:
            long_score += 0.1
            long_reasons.append(f"RSI偏低: {ctx.rsi:.1f}")
        
        # MACD 金叉/动量
        if ctx.macd > ctx.macd_signal and ctx.macd_histogram > 0:
            long_score += 0.2
            long_reasons.append("MACD金叉确认")
        
        # 成交量确认
        if ctx.volume_ratio > self.config.volume_surge_threshold:
            long_score += 0.15
            long_reasons.append(f"成交量放大: {ctx.volume_ratio:.1f}x")
        
        # 订单流确认
        if ctx.taker_buy_ratio > 0.55:
            long_score += 0.1
            long_reasons.append(f"买方主导: {ctx.taker_buy_ratio:.1%}")
        
        # ========== 空头入场分析 ==========
        short_score = 0.0
        short_reasons = []
        
        # 趋势对齐
        if ctx.ema_9 < ctx.ema_21 and ctx.current_price < ctx.vwap:
            short_score += 0.3
            short_reasons.append("趋势对齐: 9EMA < 21EMA, 价格 < VWAP")
        
        # RSI 超买回落
        if ctx.rsi > self.config.rsi_overbought:
            short_score += 0.25
            short_reasons.append(f"RSI超买: {ctx.rsi:.1f}")
            entry_timing = EntryTiming.AGGRESSIVE
        elif ctx.rsi > 55:
            short_score += 0.1
            short_reasons.append(f"RSI偏高: {ctx.rsi:.1f}")
        
        # MACD 死叉/动量
        if ctx.macd < ctx.macd_signal and ctx.macd_histogram < 0:
            short_score += 0.2
            short_reasons.append("MACD死叉确认")
        
        # 成交量确认
        if ctx.volume_ratio > self.config.volume_surge_threshold:
            short_score += 0.15
            short_reasons.append(f"成交量放大: {ctx.volume_ratio:.1f}x")
        
        # 订单流确认
        if ctx.taker_buy_ratio < 0.45:
            short_score += 0.1
            short_reasons.append(f"卖方主导: {1 - ctx.taker_buy_ratio:.1%}")
        
        # ========== 长期方向硬约束 ==========
        # 统一枚举: long_only/short_only/both（兼容旧值 long/short/any）
        _dir = self.config.allowed_direction
        if _dir in ("long_only", "long"):
            if short_score > 0:
                short_reasons.append("⛔ 长线偏多(long_only)，短线做空被约束")
            short_score = 0.0
        elif _dir in ("short_only", "short"):
            if long_score > 0:
                long_reasons.append("⛔ 长线偏空(short_only)，短线做多被约束")
            long_score = 0.0

        # 顺势加分：短线方向与长线一致时额外加置信度
        if self.config.long_term_bias == "long" and long_score > 0:
            long_score += 0.10
            long_reasons.append("📈 顺势加分：与长线多头一致")
        elif self.config.long_term_bias == "short" and short_score > 0:
            short_score += 0.10
            short_reasons.append("📉 顺势加分：与长线空头一致")

        # ========== 决策 ==========
        if long_score > short_score and long_score >= self.config.min_confidence:
            action = TacticalAction.ENTER_LONG
            confidence = long_score
            reasons = long_reasons
        elif short_score > long_score and short_score >= self.config.min_confidence:
            action = TacticalAction.ENTER_SHORT
            confidence = short_score
            reasons = short_reasons
        else:
            return None
        
        # 计算止盈止损
        if action == TacticalAction.ENTER_LONG:
            stop_loss = ctx.current_price - (ctx.atr * self.config.sl_atr_multiple)
            take_profit = ctx.current_price + (ctx.atr * self.config.tp_atr_multiple)
        else:
            stop_loss = ctx.current_price + (ctx.atr * self.config.sl_atr_multiple)
            take_profit = ctx.current_price - (ctx.atr * self.config.tp_atr_multiple)
        
        # V5.1 安全钳位：短线战术器对齐 coordinator 的 tier 上限
        sl_pct = abs(ctx.current_price - stop_loss) / ctx.current_price if ctx.current_price > 0 else 0
        tp_pct = abs(take_profit - ctx.current_price) / ctx.current_price if ctx.current_price > 0 else 0
        
        if sl_pct > self.config.max_sl_pct:
            sl_pct = self.config.max_sl_pct
            if action == TacticalAction.ENTER_LONG:
                stop_loss = ctx.current_price * (1 - sl_pct)
            else:
                stop_loss = ctx.current_price * (1 + sl_pct)
            logger.debug(f"[Tactician] SL clamped to tier cap {sl_pct:.2%}")
        
        # 盈亏比保护：TP 必须 ≥ RR × SL
        if tp_pct < sl_pct * self.config.min_rr_ratio:
            tp_pct = sl_pct * self.config.min_rr_ratio
            if action == TacticalAction.ENTER_LONG:
                take_profit = ctx.current_price * (1 + tp_pct)
            else:
                take_profit = ctx.current_price * (1 - tp_pct)
            logger.debug(f"[Tactician] TP adjusted to meet min RR {self.config.min_rr_ratio:.1f}:1")
        
        # 计算仓位
        position_size = self._calculate_position_size(confidence, ctx.atr_pct)
        
        return TacticalSignal(
            symbol=ctx.symbol,
            action=action,
            confidence=confidence,
            entry_timing=entry_timing,
            suggested_price=ctx.current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            position_size_pct=position_size,
            reasons=reasons,
            valid_until=datetime.now(timezone.utc) + timedelta(minutes=self.config.signal_valid_minutes)
        )
    
    def _analyze_exit(self, ctx: ShortTermContext) -> Optional[TacticalSignal]:
        """
        分析出场机会 — 基于技术反转信号

        检测四类出场条件：
          1. RSI 极端区反转
          2. MACD 柱缩短（动量衰减）
          3. 价格突破 EMA 逆向（趋势破坏）
          4. ATR 异常放大（波动率突变风险）

        任何一条触发都生成出场建议，多条命中叠加置信度。
        """
        exit_score = 0.0
        exit_reasons: list[str] = []
        reduce_ratio = 0.5  # 默认建议减仓 50%

        # ---- 1. RSI 极端区反转 ----
        if ctx.rsi >= self.config.rsi_extreme_high:
            exit_score += 0.30
            exit_reasons.append(f"RSI极端超买 {ctx.rsi:.1f} ≥ {self.config.rsi_extreme_high}")
            reduce_ratio = 0.75
        elif ctx.rsi <= self.config.rsi_extreme_low:
            exit_score += 0.30
            exit_reasons.append(f"RSI极端超卖 {ctx.rsi:.1f} ≤ {self.config.rsi_extreme_low}")
            reduce_ratio = 0.75

        # ---- 2. MACD 柱缩短（动量衰减） ----
        prev_signal = self._active_signals.get(ctx.symbol)
        if prev_signal and hasattr(prev_signal, '_prev_macd_hist'):
            prev_hist = prev_signal._prev_macd_hist
            cur_hist = ctx.macd_histogram
            if prev_hist != 0 and abs(cur_hist) < abs(prev_hist) * 0.5:
                exit_score += 0.20
                exit_reasons.append(
                    f"MACD柱缩短 {prev_hist:.6f}→{cur_hist:.6f}"
                )
        # 柱翻转（从正变负或反之）也是强信号
        if ctx.macd_histogram != 0 and prev_signal:
            if hasattr(prev_signal, '_prev_macd_hist') and prev_signal._prev_macd_hist != 0:
                if (ctx.macd_histogram > 0) != (prev_signal._prev_macd_hist > 0):
                    exit_score += 0.15
                    exit_reasons.append("MACD柱翻转")

        # ---- 3. 价格突破 EMA 逆向 ----
        if ctx.ema_9 > 0 and ctx.ema_21 > 0:
            # 多头持仓但短期 EMA 跌破长期 EMA
            if ctx.ema_9 < ctx.ema_21 and ctx.current_price < ctx.ema_9:
                exit_score += 0.25
                exit_reasons.append("价格跌破 9EMA < 21EMA，多头趋势破坏")
            # 空头持仓但短期 EMA 突破长期 EMA
            elif ctx.ema_9 > ctx.ema_21 and ctx.current_price > ctx.ema_9:
                exit_score += 0.25
                exit_reasons.append("价格突破 9EMA > 21EMA，空头趋势破坏")

        # ---- 4. ATR 异常放大 ----
        if ctx.atr_pct > 0.06:
            exit_score += 0.20
            exit_reasons.append(f"ATR异常放大 {ctx.atr_pct:.2%}，波动率突变")
            reduce_ratio = min(reduce_ratio + 0.25, 1.0)

        # ---- 生成出场信号 ----
        if exit_score < 0.30:
            # 记录 MACD 柱供下次对比
            self._store_macd_hist(ctx)
            return None

        self._store_macd_hist(ctx)
        return TacticalSignal(
            symbol=ctx.symbol,
            action=TacticalAction.EXIT,
            confidence=min(exit_score, 1.0),
            entry_timing=EntryTiming.AGGRESSIVE,
            suggested_price=ctx.current_price,
            stop_loss=0.0,
            take_profit=0.0,
            position_size_pct=reduce_ratio,
            reasons=exit_reasons,
            valid_until=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

    def _store_macd_hist(self, ctx: ShortTermContext):
        """暂存 MACD 柱值用于下轮对比"""
        sig = self._active_signals.get(ctx.symbol)
        if sig:
            sig._prev_macd_hist = ctx.macd_histogram  # type: ignore[attr-defined]
    
    def _make_decision(
        self,
        ctx: ShortTermContext,
        entry_signal: Optional[TacticalSignal],
        exit_signal: Optional[TacticalSignal]
    ) -> TacticalSignal:
        """综合决策 — 出场信号置信度 >= 0.6 时优先执行"""
        if exit_signal and exit_signal.confidence >= 0.6:
            return exit_signal
        
        if entry_signal:
            return entry_signal
        
        return TacticalSignal(
            symbol=ctx.symbol,
            action=TacticalAction.WAIT,
            confidence=0.0,
            entry_timing=EntryTiming.STANDARD,
            suggested_price=ctx.current_price,
            stop_loss=0.0,
            take_profit=0.0,
            position_size_pct=0.0,
            reasons=["无明确交易信号"]
        )
    
    def _calculate_position_size(self, confidence: float, atr_pct: float) -> float:
        """计算仓位大小（受长线风险预算上限约束）"""
        base_size = self.config.max_position_size * confidence
        
        # 波动率调整 (高波动降低仓位)
        vol_factor = 1.0
        if atr_pct > 0.05:
            vol_factor = 0.5
        elif atr_pct > 0.03:
            vol_factor = 0.7
        
        adjusted_size = base_size * vol_factor
        cap = min(self.config.max_position_size, self.config.long_term_max_position)
        return min(adjusted_size, cap)
    
    def get_active_signal(self, symbol: str) -> Optional[TacticalSignal]:
        """获取活跃信号"""
        signal = self._active_signals.get(symbol)
        
        # 检查有效期
        if signal and signal.valid_until:
            if datetime.now(timezone.utc) > signal.valid_until:
                del self._active_signals[symbol]
                return None
        
        return signal
    
    def get_decision_history(self, symbol: str, limit: int = 20) -> List[TacticalSignal]:
        """获取决策历史"""
        history = self._decision_history.get(symbol, [])
        return history[-limit:]
    
    def clear_signal(self, symbol: str):
        """清除信号"""
        if symbol in self._active_signals:
            del self._active_signals[symbol]
    
    def get_status(self) -> Dict[str, Any]:
        """获取状态"""
        return {
            "active_signals": len(self._active_signals),
            "tracked_symbols": len(self._decision_history),
            "config": {
                "min_confidence": self.config.min_confidence,
                "max_position_size": self.config.max_position_size,
                "signal_valid_minutes": self.config.signal_valid_minutes
            }
        }


# 全局实例
_short_term_tactician: Optional[ShortTermTactician] = None


def get_short_term_tactician() -> ShortTermTactician:
    """获取全局短期战术器"""
    global _short_term_tactician
    if _short_term_tactician is None:
        _short_term_tactician = ShortTermTactician()
    return _short_term_tactician
