"""
三维信号确认引擎 — SignalConfirmationEngine

跨维度信号多重确认：技术面、链上/订单流、情绪面三者同向才触发。
（方案§10.2）

三个维度的数据源：
  - 维度1 技术面：technical_indicators + market_regime_detector
  - 维度2 链上/订单流：derivatives_analytics_service + whale_tracker_service
  - 维度3 情绪面：sentiment_composite_service

确认规则：
  - 3 个维度全部同向 → 强确认（strong）
  - 至少 2 个维度同向 → 普通确认（normal，仓位缩减30%）
  - 不足 2 个维度同向 → HOLD
  - 维度间方向矛盾 → HOLD
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Any

logger = logging.getLogger(__name__)


@dataclass
class DimensionSignal:
    """单维度信号"""
    dimension: str = ""     # "technical" / "order_flow" / "sentiment"
    direction: int = 0          # +1 看多，-1 看空，0 中性
    strength: float = 0.0       # 0.0 ~ 1.0
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfirmationResult:
    """三维确认结果"""
    action: str                          # "BUY" / "SELL" / "HOLD"
    direction: int = 0                   # +1 / -1 / 0
    strength: float = 0.0               # 平均强度
    confirmation_level: str = "none"    # "strong" / "normal" / "none"
    confirmed_dimensions: int = 0       # 参与确认的维度数量
    position_multiplier: float = 1.0    # 仓位乘数（normal 时 0.7，strong 时 1.0）
    reason: str = ""
    dimensions: Dict[str, DimensionSignal] = field(default_factory=dict)


class SignalConfirmationEngine:
    """
    跨维度信号确认引擎。

    使用方式：
        engine = SignalConfirmationEngine()
        result = engine.evaluate(symbol="BTC", market_data={...})
    """

    def evaluate(
        self,
        symbol: str,
        klines_1h: Optional[List[Dict]] = None,
        derivatives_data: Optional[Dict] = None,
        whale_data: Optional[Dict] = None,
        sentiment_data: Optional[Dict] = None,
        regime: Optional[str] = None,
    ) -> ConfirmationResult:
        """
        评估三维信号确认结果。

        Args:
            symbol: 交易对
            klines_1h: 1小时K线数据
            derivatives_data: 衍生品数据（OI、funding rate、清算等）
            whale_data: 巨鲸追踪数据
            sentiment_data: 情绪综合数据
            regime: 市场状态（trending/ranging/volatile）

        Returns:
            ConfirmationResult
        """
        # 评估三个维度
        tech = self._evaluate_technical(symbol, klines_1h, regime)
        flow = self._evaluate_order_flow(symbol, derivatives_data, whale_data)
        sent = self._evaluate_sentiment(symbol, sentiment_data)

        tech.dimension = "technical"
        flow.dimension = "order_flow"
        sent.dimension = "sentiment"

        dimensions = {
            "technical": tech,
            "order_flow": flow,
            "sentiment": sent,
        }

        non_zero = [(d.direction, d.strength) for d in dimensions.values() if d.direction != 0]
        has_data = sum(1 for d in dimensions.values()
                       if d.direction != 0 or "无" not in d.reason)

        if len(non_zero) == 0:
            return ConfirmationResult(
                action="HOLD",
                reason="所有维度均为中性，无方向信号",
                dimensions=dimensions,
            )

        directions = [d for d, _ in non_zero]
        strengths = [s for _, s in non_zero]

        bullish_count = sum(1 for d in directions if d > 0)
        bearish_count = sum(1 for d in directions if d < 0)

        if bullish_count > 0 and bearish_count > 0:
            bull_strength = sum(s for d, s in non_zero if d > 0)
            bear_strength = sum(s for d, s in non_zero if d < 0)
            if bullish_count > bearish_count or (bullish_count == bearish_count and bull_strength > bear_strength):
                confirmed_direction = 1
                avg_strength = bull_strength / bullish_count * 0.5
            elif bearish_count > bullish_count or (bullish_count == bearish_count and bear_strength > bull_strength):
                confirmed_direction = -1
                avg_strength = bear_strength / bearish_count * 0.5
            else:
                return ConfirmationResult(
                    action="HOLD", reason="维度信号势均力敌", dimensions=dimensions,
                )
            confirmed_dims = max(bullish_count, bearish_count)
            level = "conflict_resolved"
            pos_mul = 0.4
        else:
            confirmed_direction = directions[0]
            avg_strength = sum(strengths) / len(strengths)
            confirmed_dims = len(non_zero)

            # 按「有数据的维度数」而非「总维度数」来判断确认等级
            # 如果只有1个维度有数据（其余缺失），那个维度给出信号就视为 weak 确认
            if confirmed_dims == 3:
                level = "strong"
                pos_mul = 1.0
            elif confirmed_dims == 2:
                level = "normal"
                pos_mul = 0.7
            elif confirmed_dims == 1 and has_data <= 1:
                level = "weak"
                pos_mul = 0.4
            elif confirmed_dims == 1 and has_data >= 2:
                level = "weak"
                pos_mul = 0.3
            else:
                level = "weak"
                pos_mul = 0.4

        action = "BUY" if confirmed_direction > 0 else "SELL"

        dim_reasons = " | ".join(f"{k}:{v.reason}" for k, v in dimensions.items() if v.direction != 0)
        reason = f"{confirmed_dims}维确认({level}): {dim_reasons}"

        logger.info(
            f"[ConfirmEngine] {symbol} → {action} "
            f"level={level} strength={avg_strength:.2f} dims={confirmed_dims}"
        )

        return ConfirmationResult(
            action=action,
            direction=confirmed_direction,
            strength=round(avg_strength, 3),
            confirmation_level=level,
            confirmed_dimensions=confirmed_dims,
            position_multiplier=pos_mul,
            reason=reason,
            dimensions=dimensions,
        )

    def _evaluate_technical(
        self,
        symbol: str,
        klines: Optional[List[Dict]],
        regime: Optional[str],
    ) -> DimensionSignal:
        """
        技术面维度评估。

        逻辑（方案§10.2）：
        - 趋势：EMA9 > EMA21 且价格 > EMA50 → 看多（+1）
        - 动量：RSI 40~70（非超买超卖）且 MACD 同向
        - 市场状态：trending 信号更强；volatile/ranging 降低 strength
        """
        if not klines or len(klines) < 55:
            return DimensionSignal(direction=0, strength=0, reason="K线数据不足")

        try:
            closes = [float(k.get("close", 0)) for k in klines[-60:]]
            if len(closes) < 55 or closes[-1] <= 0:
                return DimensionSignal(direction=0, strength=0, reason="K线价格无效")

            # 简化 EMA 计算
            def ema(data, period):
                k_ = 2 / (period + 1)
                val = data[0]
                for v in data[1:]:
                    val = v * k_ + val * (1 - k_)
                return val

            ema9 = ema(closes[-20:], 9)
            ema21 = ema(closes[-30:], 21)
            ema50 = ema(closes[-55:], 50)
            price = closes[-1]

            # RSI (14)
            gains, losses = [], []
            for i in range(-15, 0):
                diff = closes[i] - closes[i - 1]
                gains.append(max(diff, 0))
                losses.append(max(-diff, 0))
            avg_gain = sum(gains) / len(gains) if gains else 0
            avg_loss = sum(losses) / len(losses) if losses else 1e-9
            avg_loss = max(avg_loss, 1e-9)
            rs = avg_gain / avg_loss
            rsi = 100 - 100 / (1 + rs)

            # MACD (12,26,9)
            ema12 = ema(closes[-30:], 12)
            ema26 = ema(closes[-30:], 26)
            macd = ema12 - ema26

            # 趋势评分（降低门槛，不再要求所有条件同时成立）
            ema_bullish = ema9 > ema21
            ema_bearish = ema9 < ema21
            price_above_50 = price > ema50
            price_below_50 = price < ema50
            rsi_bullish = rsi > 50
            rsi_bearish = rsi < 50
            macd_bullish = macd > 0
            macd_bearish = macd < 0

            bull_score = sum([ema_bullish, price_above_50, rsi_bullish, macd_bullish])
            bear_score = sum([ema_bearish, price_below_50, rsi_bearish, macd_bearish])

            if bull_score >= 3:
                direction = +1
                strength = 0.8 if bull_score == 4 else 0.6
                reason = f"趋势↑({bull_score}/4) EMA9={ema9:.0f} RSI={rsi:.0f} MACD={macd:.2f}"
            elif bear_score >= 3:
                direction = -1
                strength = 0.8 if bear_score == 4 else 0.6
                reason = f"趋势↓({bear_score}/4) EMA9={ema9:.0f} RSI={rsi:.0f} MACD={macd:.2f}"
            elif bull_score == 2 and ema_bullish:
                direction = +1
                strength = 0.35
                reason = f"弱趋势↑ EMA9>EMA21 RSI={rsi:.0f}"
            elif bear_score == 2 and ema_bearish:
                direction = -1
                strength = 0.35
                reason = f"弱趋势↓ EMA9<EMA21 RSI={rsi:.0f}"
            else:
                return DimensionSignal(direction=0, strength=0, reason=f"无趋势 RSI={rsi:.0f}")

            # 市场状态调整
            if regime == "volatile":
                strength *= 0.6
                reason += " (volatile降权)"
            elif regime == "ranging":
                strength *= 0.7
                reason += " (ranging降权)"

            return DimensionSignal(direction=direction, strength=round(strength, 3), reason=reason)

        except Exception as e:
            logger.debug(f"[ConfirmEngine] _evaluate_technical error: {e}")
            return DimensionSignal(direction=0, strength=0, reason=f"技术面计算异常: {e}")

    def _evaluate_order_flow(
        self,
        symbol: str,
        derivatives: Optional[Dict],
        whale: Optional[Dict],
    ) -> DimensionSignal:
        """
        链上/订单流维度评估（方案§10.2）。

        逻辑：
        - OI 1h 变化 > +3% 且价格上涨 → 看多（新多头入场）
        - 资金费率 > +0.05% → 多头拥挤，反向看空
        - 巨鲸方向同向加分
        """
        if not derivatives:
            return DimensionSignal(direction=0, strength=0, reason="无衍生品数据")

        try:
            oi_change_1h = float(derivatives.get("oi_change_1h_pct", 0))
            funding_rate = float(derivatives.get("funding_rate", 0))
            price_change_1h = float(derivatives.get("price_change_1h_pct", 0))
            whale_direction = int(whale.get("direction", 0)) if whale else 0

            scores = []
            reasons = []

            # OI 与价格同向（降低阈值 3%→1%）
            if oi_change_1h > 1.0 and price_change_1h > 0:
                scores.append(+1)
                reasons.append(f"OI+{oi_change_1h:.1f}%多头入场")
            elif oi_change_1h > 1.0 and price_change_1h < 0:
                scores.append(-1)
                reasons.append(f"OI+{oi_change_1h:.1f}%空头入场")
            elif oi_change_1h < -1.0:
                if price_change_1h > 0:
                    scores.append(+1)
                    reasons.append(f"OI减少+价涨→空头减仓偏多")
                elif price_change_1h < 0:
                    scores.append(-1)
                    reasons.append(f"OI减少+价跌→多头减仓偏空")

            # 资金费率（降低阈值 0.05%→0.02%）
            if funding_rate > 0.0002:
                scores.append(-1)
                reasons.append(f"资金费率{funding_rate*100:.3f}%多头拥挤→反向")
            elif funding_rate < -0.0002:
                scores.append(+1)
                reasons.append(f"资金费率{funding_rate*100:.3f}%空头拥挤→反向")

            # 巨鲸方向
            if whale_direction != 0:
                scores.append(whale_direction)
                reasons.append(f"巨鲸方向{'多' if whale_direction > 0 else '空'}")

            if not scores:
                return DimensionSignal(direction=0, strength=0, reason="订单流中性")

            net = sum(scores)
            if net > 0:
                direction = +1
                strength = min(0.9, 0.5 + 0.15 * net)
            elif net < 0:
                direction = -1
                strength = min(0.9, 0.5 + 0.15 * abs(net))
            else:
                return DimensionSignal(direction=0, strength=0, reason="订单流信号抵消")

            return DimensionSignal(
                direction=direction,
                strength=round(strength, 3),
                reason=" | ".join(reasons),
            )
        except Exception as e:
            logger.debug(f"[ConfirmEngine] _evaluate_order_flow error: {e}")
            return DimensionSignal(direction=0, strength=0, reason=f"订单流计算异常: {e}")

    def _evaluate_sentiment(
        self,
        symbol: str,
        sentiment: Optional[Dict],
    ) -> DimensionSignal:
        """
        情绪面维度评估（方案§10.2）。

        逻辑：
        - 情绪指数 70~85 → 偏多（+1），> 85 → 过热反向（-1）
        - 情绪指数 15~30 → 偏空（-1），< 15 → 极端恐慌反向（+1）
        - 30~70 → 中性（0）
        """
        if not sentiment:
            return DimensionSignal(direction=0, strength=0, reason="无情绪数据")

        try:
            index = float(sentiment.get("composite_index", sentiment.get("index", 50)))
            reasons = []

            if index > 85:
                direction = -1
                strength = 0.8
                reason = f"极端贪婪({index:.0f})→反向看空"
            elif index >= 60:
                direction = +1
                strength = 0.5 + (index - 60) / 50
                reason = f"情绪偏多({index:.0f})"
            elif index < 15:
                direction = +1
                strength = 0.8
                reason = f"极端恐慌({index:.0f})→反向看多"
            elif index <= 40:
                direction = -1
                strength = 0.5 + (40 - index) / 50
                reason = f"情绪偏空({index:.0f})"
            else:
                return DimensionSignal(direction=0, strength=0, reason=f"情绪中性({index:.0f})")

            return DimensionSignal(direction=direction, strength=strength, reason=reason)
        except Exception as e:
            logger.debug(f"[ConfirmEngine] _evaluate_sentiment error: {e}")
            return DimensionSignal(direction=0, strength=0, reason=f"情绪计算异常: {e}")


# 模块级单例
signal_confirmation_engine = SignalConfirmationEngine()
