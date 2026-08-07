"""AI因子: 弱势动量 | 置信:60% | 短期动量减去长期动量，经tanh归一化。反映价格短期相对长期趋势的弱势程度，正值表示短期强于长期（看多），负值表示短期弱于长期（看空）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Weak_Momentum(BaseFactor):
    """短期动量减去长期动量，经tanh归一化。反映价格短期相对长期趋势的弱势程度，正值表示短期强于长期（看多），负值表示短期弱于长期（看空）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wk_mom",
            name="Weak Momentum",
            display_name="弱势动量",
            description="短期动量减去长期动量，经tanh归一化。反映价格短期相对长期趋势的弱势程度，正值表示短期强于长期（看多），负值表示短期弱于长期（看空）。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        short_period = 5
        long_period = 20
        short_ret = data['close'].pct_change(short_period)
        long_ret = data['close'].pct_change(long_period)
        diff = short_ret - long_ret
        # 归一化到[-1,1] 使用tanh
        result = np.tanh(diff * 10)  # 系数调整灵敏度
        return result.fillna(0)
