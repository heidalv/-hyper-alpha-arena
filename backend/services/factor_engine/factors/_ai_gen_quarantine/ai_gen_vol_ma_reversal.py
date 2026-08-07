"""AI因子: 波动率调整均值回归 | 置信:60% | 计算价格相对于近期移动均线的偏离，并用波动率缩放，当偏离过大且成交量萎缩时产生卖出信号，避免追高被套。适用于高波动环境下的假突破识别。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_Mean_Reversion(BaseFactor):
    """计算价格相对于近期移动均线的偏离，并用波动率缩放，当偏离过大且成交量萎缩时产生卖出信号，避免追高被套。适用于高波动环境下的假突破识别。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_ma_reversal",
            name="Volatility-Adjusted Mean Reversion",
            display_name="波动率调整均值回归",
            description="计算价格相对于近期移动均线的偏离，并用波动率缩放，当偏离过大且成交量萎缩时产生卖出信号，避免追高被套。适用于高波动环境下的假突破识别。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 参数
        lookback = 20
        vol_lookback = 20
        # 计算典型价格
        tp = (data['high'] + data['low'] + data['close']) / 3
        # 移动均线
        ma = tp.rolling(lookback).mean()
        # 波动率（ATR简化）
        atr = (data['high'] - data['low']).rolling(vol_lookback).mean()
        # 偏离程度
        deviation = (tp - ma) / (atr + 1e-8)
        # 成交量萎缩指示（低于近10日均值）
        vol_ratio = data['volume'] / data['volume'].rolling(10).mean()
        shrink = (vol_ratio < 0.8).astype(float)
        # 组合信号：正偏离过大且缩量时看空（-1），负偏离过大且放量时看多（+1）
        signal = -deviation * shrink
        # 截断到[-1,1]
        result = signal.clip(-1, 1)
        return result
