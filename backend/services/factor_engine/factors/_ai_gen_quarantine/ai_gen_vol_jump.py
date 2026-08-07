"""AI因子: 波动率跳跃因子 | 置信:60% | 计算短期波动率与长期波动率的比值，识别波动率突然放大进入未知状态，易触发止损。使用close计算收益率，短期std 5周期，长期std 20周期，比值经tanh归一化至[-1,1]。正值表示波动率上升。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityJump(BaseFactor):
    """计算短期波动率与长期波动率的比值，识别波动率突然放大进入未知状态，易触发止损。使用close计算收益率，短期std 5周期，长期std 20周期，比值经tanh归一化至[-1,1]。正值表示波动率上升。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_jump",
            name="Volatility_Jump",
            display_name="波动率跳跃因子",
            description="计算短期波动率与长期波动率的比值，识别波动率突然放大进入未知状态，易触发止损。使用close计算收益率，短期std 5周期，长期std 20周期，比值经tanh归一化至[-1,1]。正值表示波动率上升。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        ret = close.pct_change()
        short_vol = ret.rolling(5).std()
        long_vol = ret.rolling(20).std()
        ratio = short_vol / (long_vol + 1e-10)
        # 归一化到[-1,1]，使用tanh
        result = np.tanh(ratio - 1)  # 中心化到0附近
        return result.fillna(0)
