"""AI因子: 价格扭曲因子 | 置信:60% | 识别价格在窄幅震荡后突然出现的异常波动，这种扭曲往往导致止损/止盈被意外触发。通过计算当前价格相对于近期价格区间的分位数与ATR的突变程度，量化市场无序性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Distortion_Factor(BaseFactor):
    """识别价格在窄幅震荡后突然出现的异常波动，这种扭曲往往导致止损/止盈被意外触发。通过计算当前价格相对于近期价格区间的分位数与ATR的突变程度，量化市场无序性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pdf",
            name="Price Distortion Factor",
            display_name="价格扭曲因子",
            description="识别价格在窄幅震荡后突然出现的异常波动，这种扭曲往往导致止损/止盈被意外触发。通过计算当前价格相对于近期价格区间的分位数与ATR的突变程度，量化市场无序性。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算近期价格区间
        period = 20
        high = data['high'].rolling(period).max()
        low = data['low'].rolling(period).min()
        mid = (high + low) / 2
        # 价格位置分位数，0~1
        pos = (data['close'] - low) / (high - low + 1e-10)
        # 计算ATR
        tr = pd.concat([data['high'] - data['low'], 
                        (data['high'] - data['close'].shift(1)).abs(),
                        (data['low'] - data['close'].shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        delta_atr = (tr - atr) / (atr + 1e-10)
        # 组合因子：价格位置接近0.5且ATR突然放大时为高风险
        dist = (pos - 0.5).abs() * 2  # 0~1
        factor = np.where(dist < 0.3, delta_atr, -delta_atr)  # 靠近中间时取正值，否则取负
        # 标准化到[-1,1]
        factor = np.clip(factor, -2, 2) / 2
        return pd.Series(factor, index=data.index)
