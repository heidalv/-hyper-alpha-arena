"""AI因子: 波动不确定性指数 | 置信:70% | 基于布林带宽度与ATR的变异系数衡量市场不确定性。高不确定性时值为负，提示不宜开仓；低不确定性时值为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Uncertainty_Index(BaseFactor):
    """基于布林带宽度与ATR的变异系数衡量市场不确定性。高不确定性时值为负，提示不宜开仓；低不确定性时值为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_uncertainty",
            name="Volatility Uncertainty Index",
            display_name="波动不确定性指数",
            description="基于布林带宽度与ATR的变异系数衡量市场不确定性。高不确定性时值为负，提示不宜开仓；低不确定性时值为正。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 布林带宽度
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        bandwidth = 2 * std / ma
        # 变异系数：atr的滚动标准差/均值
        atr_rolling_mean = atr.rolling(14).mean()
        atr_rolling_std = atr.rolling(14).std()
        cv = atr_rolling_std / (atr_rolling_mean + 1e-10)
        # 综合不确定性得分
        uncertainty = (bandwidth + cv) / 2
        # 归一化到[-1,1]，使用z-score然后tanh
        z = (uncertainty - uncertainty.rolling(60).mean()) / (uncertainty.rolling(60).std() + 1e-10)
        result = -np.tanh(z)  # 高不确定性 => 负值
        return result.fillna(0)
