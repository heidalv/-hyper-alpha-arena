"""
规则引擎决策层 — RuleBasedDecisionEngine

替代 LLM 直接输出 BUY/SELL/HOLD 的危险方式。
最终交易决策由规则引擎执行，LLM 只负责情绪分数和黑天鹅检测。
（方案§11.3）

决策流程：
1. 信号确认检查（signal_confirmation_engine）
2. 风控检查（risk_control_service）
3. 仓位计算（position_sizer）
4. LLM 情绪加权（只影响仓位，不影响方向）
5. 生成最终决策
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMSentimentInput:
    """LLM 情绪输入（只作为辅助因子，不决定方向）"""
    score: float = 0.0           # -1.0 到 +1.0
    is_black_swan: bool = False  # 黑天鹅事件检测
    black_swan_reason: str = ""
    confidence: float = 0.5      # LLM 置信度（低置信度时权重降低）


@dataclass
class RuleDecision:
    """规则引擎输出的最终决策"""
    action: str                          # "BUY" / "SELL" / "HOLD" / "EMERGENCY_CLOSE_ALL"
    symbol: str = ""
    direction: int = 0                   # +1 / -1
    position_size_usd: float = 0.0      # 建议仓位（美元名义）
    leverage: float = 10.0              # 合约杠杆（8x-20x）
    margin_usd: float = 0.0
    stop_loss_pct: float = 0.02         # 默认 2% 止损
    take_profit_pct: float = 0.06       # 默认 6% 止盈
    confidence: float = 0.0             # 综合置信度
    reason: str = ""
    blocked_by: str = ""                # 被哪个规则拦截
    decision_source: str = "rule_engine"  # D1: "rule_engine" | "llm" | "hybrid"
    details: Dict[str, Any] = field(default_factory=dict)


class RuleBasedDecisionEngine:
    """
    规则引擎决策层。

    使用方式：
        engine = RuleBasedDecisionEngine()
        decision = engine.decide(
            symbol="BTC",
            confirmation=signal_confirmation_engine.evaluate(...),
            position_sizing=position_sizer.calculate_position_size(...),
            risk_check=(True, []),
            llm_sentiment=LLMSentimentInput(score=0.3),
        )
    """

    def _calc_atr_pct(self, symbol: str, period: str = "1h", lookback: int = 14) -> float:
        """从数据库获取 ATR 百分比（ATR / close），无数据时返回默认值"""
        try:
            from backend.database.connection import MarketSessionLocal
            from backend.database.models import CryptoKline
            with MarketSessionLocal() as db:
                # M1 收口：统一 K 线查询门面（数据中心）
                from backend.services.kline_data_service import kline_service as _ks
                klines = _ks.query_klines(
                    symbol.upper(), period, limit=lookback + 1, order="desc",
                )
                if not klines or len(klines) < lookback:
                    return 0.015
                rows = list(reversed(klines))
                tr_list = []
                for i in range(1, len(rows)):
                    h = float(rows[i].get("high") or 0)
                    l = float(rows[i].get("low") or 0)
                    prev_c = float(rows[i - 1].get("close") or 0)
                    if prev_c <= 0:
                        continue
                    tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                    tr_list.append(tr)
                if not tr_list:
                    return 0.015
                atr = sum(tr_list[-lookback:]) / min(len(tr_list), lookback)
                last_close = float(rows[-1].get("close") or 1)
                return atr / last_close if last_close > 0 else 0.015
        except Exception:
            return 0.015

    def decide(
        self,
        symbol: str,
        confirmation,           # ConfirmationResult from signal_confirmation_engine
        position_sizing,        # PositionSizeResult from position_sizer
        risk_check: tuple,      # (passed: bool, responses: list)
        llm_sentiment: Optional[LLMSentimentInput] = None,
        current_price: float = 0.0,
    ) -> RuleDecision:
        """
        生成最终交易决策。

        Args:
            confirmation: 三维信号确认结果
            position_sizing: 仓位计算结果
            risk_check: (是否通过, 检查详情列表)
            llm_sentiment: LLM 情绪输入（可选）
            current_price: 当前价格（用于计算止损/止盈价格）
        """
        # 1. 黑天鹅事件检测（最高优先级）
        if llm_sentiment and llm_sentiment.is_black_swan:
            logger.critical(
                f"[RuleEngine] 黑天鹅事件检测: {llm_sentiment.black_swan_reason}"
            )
            return RuleDecision(
                action="EMERGENCY_CLOSE_ALL",
                symbol=symbol,
                reason=f"LLM检测到黑天鹅事件: {llm_sentiment.black_swan_reason}",
            )

        # 2. 信号确认检查（允许弱确认以降低门槛通过）
        if confirmation.action == "HOLD" or confirmation.direction == 0:
            # 如果 LLM 有强烈情绪倾向（绝对值>0.4），允许以最小仓位通过
            if llm_sentiment and abs(llm_sentiment.score) >= 0.4 and llm_sentiment.confidence >= 0.5:
                logger.info(
                    f"[RuleEngine] {symbol}: 三维确认=HOLD 但 LLM 情绪强({llm_sentiment.score:.2f})，降级放行"
                )
                degraded_dir = 1 if llm_sentiment.score > 0 else -1
                degraded_action = "BUY" if degraded_dir > 0 else "SELL"
                degraded_size = position_sizing.position_size_usd * 0.3 if position_sizing else 0
                return RuleDecision(
                    action=degraded_action,
                    symbol=symbol,
                    direction=degraded_dir,
                    position_size_usd=degraded_size,
                    leverage=8.0,
                    confidence=0.25,
                    reason=f"LLM降级放行(情绪={llm_sentiment.score:.2f}): {confirmation.reason}",
                    blocked_by="",
                )
            return RuleDecision(
                action="HOLD",
                symbol=symbol,
                reason=confirmation.reason,
                blocked_by="signal_confirmation",
            )

        # 3. 风控检查
        risk_passed, risk_responses = risk_check
        if not risk_passed:
            block_msgs = [r.message for r in risk_responses if hasattr(r, "result") and str(r.result).endswith("BLOCKED")]
            return RuleDecision(
                action="HOLD",
                symbol=symbol,
                reason=f"风控拦截: {'; '.join(block_msgs)}",
                blocked_by="risk_control",
            )

        # 4. 仓位检查
        if position_sizing.blocked or position_sizing.position_size_usd <= 0:
            return RuleDecision(
                action="HOLD",
                symbol=symbol,
                reason=f"仓位为零（{position_sizing.reason}）",
                blocked_by="position_sizer",
            )

        # 5. LLM 情绪加权（只影响仓位大小）
        position_size = position_sizing.position_size_usd
        leverage = position_sizing.leverage
        margin = position_sizing.margin_usd
        llm_weight_applied = False

        if llm_sentiment and llm_sentiment.confidence >= 0.5:
            # 情绪与信号方向矛盾时，缩小仓位 50%
            sentiment_score = llm_sentiment.score
            if (confirmation.direction > 0 and sentiment_score < -0.5) or \
               (confirmation.direction < 0 and sentiment_score > 0.5):
                position_size *= 0.5
                margin *= 0.5
                llm_weight_applied = True
                logger.info(
                    f"[RuleEngine] LLM 情绪({sentiment_score:.2f}) 与信号方向矛盾，仓位缩减50%"
                )

        # 6. 综合置信度（信号强度 × 维度确认数量）
        level_factors = {"strong": 1.0, "normal": 0.8, "weak": 0.5, "conflict_resolved": 0.4}
        dim_factor = level_factors.get(confirmation.confirmation_level, 0.6)
        confidence = confirmation.strength * dim_factor

        # 7. 止损/止盈估算（基于 ATR 动态计算）
        atr_pct = self._calc_atr_pct(symbol)
        # SL = ATR × 1.5 倍，至少 1%，信号越强止损越紧
        strength_factor = 1.0 - confirmation.strength * 0.2  # 强信号→0.8倍ATR
        stop_loss_pct = max(0.01, min(0.06, atr_pct * 1.5 * strength_factor))
        # 风险回报比：高胜率信号 → 1:2.5，低胜率 → 1:2
        rr_ratio = 2.5 if confirmation.strength >= 0.6 else 2.0
        take_profit_pct = stop_loss_pct * rr_ratio

        action = confirmation.action   # "BUY" / "SELL"

        reason = (
            f"规则引擎确认: {confirmation.reason}"
            + (f" | LLM情绪加权" if llm_weight_applied else "")
        )

        logger.info(
            f"[RuleEngine] {symbol} → {action} "
            f"size=${position_size:.0f} lev={leverage:.1f}x "
            f"conf={confidence:.2f} sl={stop_loss_pct:.1%} tp={take_profit_pct:.1%}"
        )

        return RuleDecision(
            action=action,
            symbol=symbol,
            direction=confirmation.direction,
            position_size_usd=round(position_size, 2),
            leverage=round(leverage, 2),
            margin_usd=round(margin, 2),
            stop_loss_pct=round(stop_loss_pct, 4),
            take_profit_pct=round(take_profit_pct, 4),
            confidence=round(confidence, 3),
            reason=reason,
            details={
                "confirmation_level": confirmation.confirmation_level,
                "confirmed_dimensions": confirmation.confirmed_dimensions,
                "position_original": position_sizing.position_size_usd,
                "llm_weight_applied": llm_weight_applied,
                "risk_warnings": [
                    r.message for r in risk_responses
                    if hasattr(r, "result") and str(r.result).endswith("WARNING")
                ],
            }
        )


# 模块级单例
rule_based_decision_engine = RuleBasedDecisionEngine()
