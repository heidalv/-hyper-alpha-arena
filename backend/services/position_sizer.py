"""
仓位管理器 — PositionSizer

基于「波动率（ATR）× 信号强度」的仓位管理模型（替代原「动态杠杆」方案）。

设计依据：见《001Alpha重构修订方案》第九章。
核心逻辑：
  仓位 = (账户权益 × 单笔风险比) / ATR百分比
         × 信号强度系数
         × 连续亏损惩罚系数
         × 资金费率惩罚系数
  杠杆从仓位反推，不超过 MAX_LEVERAGE。
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class PositionSizeResult:
    """仓位计算结果"""
    position_size_usd: float           # 建议仓位（美元名义价值）
    leverage: float                    # 建议杠杆倍数
    margin_usd: float                  # 所需保证金
    risk_per_trade_pct: float          # 本笔交易风险占账户的比例
    adjustments: Dict[str, float] = field(default_factory=dict)   # 各系数明细
    blocked: bool = False              # True 表示连续亏损过多，停止交易
    reason: str = ""


class PositionSizer:
    """
    基于波动率和信号强度的仓位管理器。

    参数：
    - MAX_LEVERAGE = 20（合约杠杆上限）
    - MIN_LEVERAGE = 5（合约杠杆下限）
    - BASE_RISK_PER_TRADE = 0.015（每笔最大风险1.5%账户余额）
    """

    MAX_LEVERAGE: int = 20
    MIN_LEVERAGE: int = 5
    BASE_RISK_PER_TRADE: float = 0.015   # 1.5% 账户余额

    def calculate_position_size(
        self,
        account_equity: float,
        signal_strength: float,       # 0.0 ~ 1.0（来自 signal_confirmation_engine）
        atr_percent: float,           # ATR 占价格的百分比（0.01 = 1%）
        funding_rate: float = 0.0,    # 当前资金费率（0.0001 = 0.01%）
        consecutive_losses: int = 0,  # 连续亏损次数
        atr_median_pct: float = 0.0,  # F1-6: 历史ATR中位数百分比（0=不调整）
    ) -> PositionSizeResult:
        """
        计算建议仓位。

        Returns:
            PositionSizeResult：包含仓位大小、杠杆、各调节系数明细。
        """
        if account_equity <= 0:
            return PositionSizeResult(
                position_size_usd=0, leverage=1, margin_usd=0,
                risk_per_trade_pct=0, blocked=True, reason="账户权益为0"
            )

        # 1. 基础仓位 = 风险额度 / 波动率
        risk_amount = account_equity * self.BASE_RISK_PER_TRADE
        # atr_percent 为0时仓位视为0
        if atr_percent <= 0:
            atr_percent = 0.01   # 默认 1% 避免除零
        base_position = risk_amount / atr_percent

        # 2. 信号强度调节（最低保留 30% 仓位）
        signal_multiplier = max(0.30, min(1.0, signal_strength))

        # 3. 连续亏损惩罚系数
        if consecutive_losses >= 5:
            loss_multiplier = 0.0   # 完全停止
        elif consecutive_losses >= 3:
            loss_multiplier = 0.5   # 缩仓 50%
        else:
            loss_multiplier = 1.0

        # 4. 资金费率惩罚（费率极端时降低仓位）
        abs_fr = abs(funding_rate)
        if abs_fr > 0.001:        # > 0.1%
            funding_penalty = 0.3
        elif abs_fr > 0.0005:     # > 0.05%
            funding_penalty = 0.7
        else:
            funding_penalty = 1.0

        # 5. F1-6: 波动率自适应缩放（当前ATR vs 历史ATR中位数）
        volatility_scale = 1.0
        if atr_median_pct > 0 and atr_percent > 0:
            vol_ratio = atr_percent / atr_median_pct
            if vol_ratio > 2.0:
                volatility_scale = 0.7
                logger.info(f"[PositionSizer] 高波动({vol_ratio:.1f}x中位数) → 仓位×0.7")
            elif vol_ratio > 1.5:
                volatility_scale = 0.85
                logger.info(f"[PositionSizer] 中高波动({vol_ratio:.1f}x中位数) → 仓位×0.85")
            elif vol_ratio < 0.5:
                volatility_scale = 1.2
                logger.info(f"[PositionSizer] 低波动({vol_ratio:.1f}x中位数) → 仓位×1.2")

        # 6. 最终仓位（名义价值）
        final_position = (
            base_position
            * signal_multiplier
            * loss_multiplier
            * funding_penalty
            * volatility_scale
        )

        # 连续亏损5次直接停止
        if consecutive_losses >= 5:
            return PositionSizeResult(
                position_size_usd=0, leverage=1, margin_usd=0,
                risk_per_trade_pct=0, blocked=True,
                reason=f"连续亏损 {consecutive_losses} 次（≥5），系统暂停开仓"
            )

        # 6. 杠杆：合约交易 5x~20x，根据信号强度和波动率动态分配
        if signal_strength >= 0.8 and atr_percent < 0.015:
            leverage = float(self.MAX_LEVERAGE)                        # 极强信号+低波动 → 20x
        elif signal_strength >= 0.7 and atr_percent < 0.02:
            leverage = 15.0                                            # 强信号+较低波动 → 15x
        elif signal_strength >= 0.6 and atr_percent < 0.03:
            leverage = 12.0                                            # 强信号 → 12x
        elif signal_strength >= 0.4:
            leverage = 8.0                                             # 中等信号 → 8x
        else:
            leverage = float(self.MIN_LEVERAGE)                        # 弱信号 → 5x（底线）
        final_position = final_position * leverage

        # 最终仓位不超过账户权益 × MAX_LEVERAGE
        final_position = min(final_position, account_equity * self.MAX_LEVERAGE)

        # 7. 保证金 = 名义价值 / 杠杆
        margin_usd = final_position / leverage if leverage > 0 else final_position

        logger.debug(
            f"[PositionSizer] equity={account_equity:.0f} atr={atr_percent:.2%} "
            f"signal={signal_strength:.2f} fr={funding_rate:.4f} cons_loss={consecutive_losses} "
            f"→ position={final_position:.0f} leverage={leverage:.1f}x"
        )

        return PositionSizeResult(
            position_size_usd=round(final_position, 2),
            leverage=round(leverage, 2),
            margin_usd=round(margin_usd, 2),
            risk_per_trade_pct=self.BASE_RISK_PER_TRADE,
            blocked=False,
            adjustments={
                "signal": signal_multiplier,
                "loss": loss_multiplier,
                "funding": funding_penalty,
                "volatility": volatility_scale,
                "base_position": round(base_position, 2),
            }
        )


# 模块级单例
position_sizer = PositionSizer()
