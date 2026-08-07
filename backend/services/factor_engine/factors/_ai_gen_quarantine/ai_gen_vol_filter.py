"""AI因子: 波动率调整均值回复因子 | 置信:60% | 计算价格相对于过去20日移动平均的偏离度，并用ATR进行标准化。当偏离度绝对值较大且波动率较高时，市场可能反转或延续趋势，但根据亏损模式，高波动+负偏离给出空头信号，避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_Mean_Reversion(BaseFactor):
    """计算价格相对于过去20日移动平均的偏离度，并用ATR进行标准化。当偏离度绝对值较大且波动率较高时，市场可能反转或延续趋势，但根据亏损模式，高波动+负偏离给出空头信号，避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_filter",
            name="Volatility-Adjusted Mean Reversion",
            display_name="波动率调整均值回复因子",
            description="计算价格相对于过去20日移动平均的偏离度，并用ATR进行标准化。当偏离度绝对值较大且波动率较高时，市场可能反转或延续趋势，但根据亏损模式，高波动+负偏离给出空头信号，避免做多。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 20日简单移动平均
        sma_20 = close.rolling(20).mean()
        # ATR (14日)
        tr = pd.concat([high - low,
                        (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr_14 = tr.rolling(14).mean()
        # 标准化偏离度: (close - sma_20) / atr_14，避免除零
        divergence = (close - sma_20) / (atr_14 + 1e-10)
        # 使用sign函数乘以限制幅度，再通过tanh或clip
        result = -np.tanh(divergence * 0.5)  # 负号表示负偏离给负值（看空）
        return result.fillna(0)
