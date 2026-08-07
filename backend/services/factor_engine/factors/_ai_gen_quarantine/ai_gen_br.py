"""AI因子: 价格乖离率 | 置信:70% | 衡量收盘价与20周期简单移动平均的偏离程度，经滚动标准差标准化。当乖离率过高（正值大）时，价格回归均线的概率增加，做多风险大，因子转为负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bias_Ratio_Price_vs_MA(BaseFactor):
    """衡量收盘价与20周期简单移动平均的偏离程度，经滚动标准差标准化。当乖离率过高（正值大）时，价格回归均线的概率增加，做多风险大，因子转为负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_br",
            name="Bias Ratio (Price vs MA)",
            display_name="价格乖离率",
            description="衡量收盘价与20周期简单移动平均的偏离程度，经滚动标准差标准化。当乖离率过高（正值大）时，价格回归均线的概率增加，做多风险大，因子转为负值。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        zscore = (close - ma) / std
        # 将zscore映射到[-1,1]，使用双曲正切函数
        factor = -np.tanh(zscore / 2)
        return factor.fillna(0)
