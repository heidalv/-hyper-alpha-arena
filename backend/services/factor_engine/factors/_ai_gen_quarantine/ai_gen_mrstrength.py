"""AI因子: 均值回归强度因子 | 置信:60% | 衡量价格偏离均线的程度，偏离越大回归概率越高。使用Z-score方法，将当前价格相对N日均线的偏离标准化，并反转符号使得超买时正值（做空信号）、超卖时负值（做多信号）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Strength(BaseFactor):
    """衡量价格偏离均线的程度，偏离越大回归概率越高。使用Z-score方法，将当前价格相对N日均线的偏离标准化，并反转符号使得超买时正值（做空信号）、超卖时负值（做多信号）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrstrength",
            name="Mean Reversion Strength",
            display_name="均值回归强度因子",
            description="衡量价格偏离均线的程度，偏离越大回归概率越高。使用Z-score方法，将当前价格相对N日均线的偏离标准化，并反转符号使得超买时正值（做空信号）、超卖时负值（做多信号）。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        n = 20
        ma = close.rolling(n).mean()
        std = close.rolling(n).std()
        z = (close - ma) / std
        result = -z.clip(-3, 3) / 3.0
        return result
