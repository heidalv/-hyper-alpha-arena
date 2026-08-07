"""AI因子: 波动率状态突变 | 置信:60% | 衡量当前短期波动率相对于长期波动率的异常变化。当短期波动率突然远高于或低于长期均值时，市场可能进入未知状态，导致策略失效。使用最近N日真实波幅均值除以过去M日真实波幅均值，经sigmoid映射到[-1,1]，正值表示波动率飙升（风险），负值表示波动率骤降（可能流动性枯竭）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Regime_Shift(BaseFactor):
    """衡量当前短期波动率相对于长期波动率的异常变化。当短期波动率突然远高于或低于长期均值时，市场可能进入未知状态，导致策略失效。使用最近N日真实波幅均值除以过去M日真实波幅均值，经sigmoid映射到[-1,1]，正值表示波动率飙升（风险），负值表示波动率骤降（可能流动性枯竭）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volshift",
            name="Volatility Regime Shift",
            display_name="波动率状态突变",
            description="衡量当前短期波动率相对于长期波动率的异常变化。当短期波动率突然远高于或低于长期均值时，市场可能进入未知状态，导致策略失效。使用最近N日真实波幅均值除以过去M日真实波幅均值，经sigmoid映射到[-1,1]，正值表示波动率飙升（风险），负值表示波动率骤降（可能流动性枯竭）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        short_atr = tr.rolling(5).mean()
        long_atr = tr.rolling(30).mean()
        ratio = short_atr / long_atr
        # 归一化到[-1,1]，使用log变换+tanh
        log_ratio = np.log(ratio)
        result = np.tanh(log_ratio * 2)  # 调整幅度
        return result.fillna(0).clip(-1,1)
