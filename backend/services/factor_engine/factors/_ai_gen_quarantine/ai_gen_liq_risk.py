"""AI因子: 流动性风险因子 | 置信:60% | 识别流动性不足的时段，流动性差时小量交易即可引发价格剧烈波动，增加未知状态下的止损风险。使用修正的Amihud非流动性指标：每日价格绝对回报与成交金额的比值，再取负值并归一化。正值表示流动性良好，负值表示流动性枯竭。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Liquidity_Risk_Factor(BaseFactor):
    """识别流动性不足的时段，流动性差时小量交易即可引发价格剧烈波动，增加未知状态下的止损风险。使用修正的Amihud非流动性指标：每日价格绝对回报与成交金额的比值，再取负值并归一化。正值表示流动性良好，负值表示流动性枯竭。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_risk",
            name="Liquidity Risk Factor",
            display_name="流动性风险因子",
            description="识别流动性不足的时段，流动性差时小量交易即可引发价格剧烈波动，增加未知状态下的止损风险。使用修正的Amihud非流动性指标：每日价格绝对回报与成交金额的比值，再取负值并归一化。正值表示流动性良好，负值表示流动性枯竭。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算每日价格变动绝对值
        price_change = np.abs(close.pct_change())
        # Amihud非流动性: |return| / (volume * close) 近似，用成交额接近
        amihud = price_change / (volume * close + 1e-10)
        # 取负值，使得流动性差时低值
        neg_amihud = -amihud
        # 滚动标准化到[-1,1]
        ma = neg_amihud.rolling(20).mean()
        std = neg_amihud.rolling(20).std()
        result = (neg_amihud - ma) / (std + 1e-10)
        # 使用tanh压缩
        result = np.tanh(result)
        return result.fillna(0).clip(-1, 1)
