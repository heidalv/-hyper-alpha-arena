"""AI因子: 波动率状态切换风险 | 置信:60% | 基于短期波动率与长期波动率的比值，以及价格与近期区间的相对位置，识别市场可能从已知状态切换到未知状态的风险。当比值异常且价格处于极端位置时，输出正值表示高风险；反之为负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeShift(BaseFactor):
    """基于短期波动率与长期波动率的比值，以及价格与近期区间的相对位置，识别市场可能从已知状态切换到未知状态的风险。当比值异常且价格处于极端位置时，输出正值表示高风险；反之为负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_regime_shift",
            name="Volatility Regime Shift",
            display_name="波动率状态切换风险",
            description="基于短期波动率与长期波动率的比值，以及价格与近期区间的相对位置，识别市场可能从已知状态切换到未知状态的风险。当比值异常且价格处于极端位置时，输出正值表示高风险；反之为负值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算短期波动率（20日标准差）和长期波动率（60日标准差）
        short_vol = data['close'].pct_change().rolling(20).std()
        long_vol = data['close'].pct_change().rolling(60).std()
        # 比率，避免除零
        ratio = short_vol / (long_vol + 1e-10)
        # 价格在近期区间内的位置 (10日)
        rolling_high = data['high'].rolling(10).max()
        rolling_low = data['low'].rolling(10).min()
        price_position = (data['close'] - rolling_low) / (rolling_high - rolling_low + 1e-10)
        # 组合：比率过高且价格处于极端位置（>0.9或<0.1）时高风险
        risk = ratio.copy()
        extreme = ((price_position > 0.9) | (price_position < 0.1)).astype(float)
        risk = risk * extreme
        # 归一化到[-1,1]：使用tanh压缩
        risk = np.tanh((risk - 1) * 2)  # 当ratio=1时中性，>1正，<1负
        # 填充NaN
        risk = risk.fillna(0)
        return risk
