"""AI因子: 量价背离因子 | 置信:55% | 检测成交量放大但价格波动率未相应增加的情况，可能代表流动性陷阱或市场无效状态，此类环境下多头易亏损。当成交量显著高于均值但波动率相对平稳时，因子值为负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Dispersion_Divergence(BaseFactor):
    """检测成交量放大但价格波动率未相应增加的情况，可能代表流动性陷阱或市场无效状态，此类环境下多头易亏损。当成交量显著高于均值但波动率相对平稳时，因子值为负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_disp",
            name="Volume Dispersion Divergence",
            display_name="量价背离因子",
            description="检测成交量放大但价格波动率未相应增加的情况，可能代表流动性陷阱或市场无效状态，此类环境下多头易亏损。当成交量显著高于均值但波动率相对平稳时，因子值为负。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']

        # 参数
        vol_lookback = 20
        vol_vol_lookback = 20

        # 成交量相对变化
        vol_mean = volume.rolling(vol_lookback, min_periods=vol_lookback).mean()
        vol_ratio = volume / (vol_mean + 1e-10)
        # 成交量信号：当vol_ratio > 1.5时为正，否则为0
        vol_signal = (vol_ratio - 1.5).clip(0, 1)  # 0到1

        # 波动率：使用ATR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(vol_vol_lookback, min_periods=vol_vol_lookback).mean()

        # 波动率变化：当前ATR相对于过去20天的ATR均值
        atr_mean = atr.rolling(vol_vol_lookback, min_periods=vol_vol_lookback).mean()
        atr_change = (atr - atr_mean) / (atr_mean + 1e-10)
        # 波动率信号：当波动率减小或平稳时为正（即变化为负或零时），转换为0~1
        vol_down_signal = (-atr_change).clip(0, 1)  # atr_change负则正

        # 组合：当成交量放大但波动率未放大时产生负信号
        signal = - vol_signal * vol_down_signal

        return signal.clip(-1, 1)
