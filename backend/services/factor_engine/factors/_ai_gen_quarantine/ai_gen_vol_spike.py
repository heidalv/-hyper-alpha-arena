"""AI因子: 波动率异常预警因子 | 置信:55% | 基于ATR历史分位数，当当前ATR处于极端高位且价格出现反向运动时，预示市场不稳定，容易触发止损，因此输出负值警告。当ATR低位且趋势平稳时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySpikeWarning(BaseFactor):
    """基于ATR历史分位数，当当前ATR处于极端高位且价格出现反向运动时，预示市场不稳定，容易触发止损，因此输出负值警告。当ATR低位且趋势平稳时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_spike",
            name="Volatility Spike Warning",
            display_name="波动率异常预警因子",
            description="基于ATR历史分位数，当当前ATR处于极端高位且价格出现反向运动时，预示市场不稳定，容易触发止损，因此输出负值警告。当ATR低位且趋势平稳时输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算14日ATR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 计算ATR的50日历史分位数
        atr_rank = atr.rolling(50).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=False)
        # 当前价格趋势方向（1日变化）
        price_change = close.pct_change()
        # 当ATR分位数>0.9且价格下跌时，认为高波动下行风险大；ATR分位数<0.1且价格上涨时，低波动上行
        signal = pd.Series(0.0, index=close.index)
        high_vol = atr_rank > 0.9
        low_vol = atr_rank < 0.1
        signal[high_vol & (price_change < -0.01)] = -1.0
        signal[low_vol & (price_change > 0.01)] = 1.0
        return signal
