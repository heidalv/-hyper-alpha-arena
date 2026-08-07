"""AI因子: 波动率稳定性 | 置信:70% | 衡量当前波动率相对于历史均值的偏离程度。当波动率异常偏高时，价格容易快速反向运动导致止损，因此因子值为负指示高风险。使用过去20日的真实波幅(ATR)与过去60日的ATR中位数比值，归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityStability(BaseFactor):
    """衡量当前波动率相对于历史均值的偏离程度。当波动率异常偏高时，价格容易快速反向运动导致止损，因此因子值为负指示高风险。使用过去20日的真实波幅(ATR)与过去60日的ATR中位数比值，归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volat",
            name="Volatility Stability",
            display_name="波动率稳定性",
            description="衡量当前波动率相对于历史均值的偏离程度。当波动率异常偏高时，价格容易快速反向运动导致止损，因此因子值为负指示高风险。使用过去20日的真实波幅(ATR)与过去60日的ATR中位数比值，归一化到[-1,1]。",
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
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr20 = tr.rolling(20).mean()
        atr60_med = tr.rolling(60).median()
        ratio = atr20 / atr60_med
        # 将ratio映射到[-1,1]，正常范围0.5~2，取log后tanh
        log_ratio = np.log(ratio.clip(0.1, 10))
        result = -np.tanh(log_ratio * 2)  # 负值表示高波动风险
        return result.fillna(0.0)
