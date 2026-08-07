"""AI因子: 流动性风险因子 | 置信:60% | 基于Amihud非流动性指标的反向指标，衡量市场流动性风险。当日收益率绝对值与成交额比值高时(流动性差)，因子值为负；反之流动好则正。亏损模式中的dust_cleanup和liq_magnet_reversal与低流动性相关。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityRiskFactor(BaseFactor):
    """基于Amihud非流动性指标的反向指标，衡量市场流动性风险。当日收益率绝对值与成交额比值高时(流动性差)，因子值为负；反之流动好则正。亏损模式中的dust_cleanup和liq_magnet_reversal与低流动性相关。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lr",
            name="Liquidity Risk Factor",
            display_name="流动性风险因子",
            description="基于Amihud非流动性指标的反向指标，衡量市场流动性风险。当日收益率绝对值与成交额比值高时(流动性差)，因子值为负；反之流动好则正。亏损模式中的dust_cleanup和liq_magnet_reversal与低流动性相关。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        volume = data['volume']
        close = data['close']
        ret_abs = close.pct_change().abs()
        # 避免除以0
        amihud = (ret_abs / (volume * close)).replace([np.inf, -np.inf], np.nan)
        mean_amihud = amihud.rolling(20).mean()
        # 标准化: 高amihud对应流动性差，取负值
        # 使用排名归一化到[-1,1]
        rank = mean_amihud.rank(pct=True)  # 0~1
        result = -1 + 2 * rank
        return result.fillna(0).clip(-1, 1)
