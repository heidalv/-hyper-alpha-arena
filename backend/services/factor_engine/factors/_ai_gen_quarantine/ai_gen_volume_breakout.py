"""AI因子: 成交量确认突破因子 | 置信:60% | 检测价格是否突破近期通道（如布林带或唐奇安通道），同时要求成交量显著高于均值，以过滤假突破。该因子对横盘或未知市场中的假突破较为敏感，可减少止损触发次数。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Confirmed_Breakout(BaseFactor):
    """检测价格是否突破近期通道（如布林带或唐奇安通道），同时要求成交量显著高于均值，以过滤假突破。该因子对横盘或未知市场中的假突破较为敏感，可减少止损触发次数。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_breakout",
            name="Volume Confirmed Breakout",
            display_name="成交量确认突破因子",
            description="检测价格是否突破近期通道（如布林带或唐奇安通道），同时要求成交量显著高于均值，以过滤假突破。该因子对横盘或未知市场中的假突破较为敏感，可减少止损触发次数。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 计算20周期高位和低位
        rolling_high = high.rolling(20).max()
        rolling_low = low.rolling(20).min()
        mid = (rolling_high + rolling_low) / 2
        # 价格相对位置
        price_pos = (close - rolling_low) / (rolling_high - rolling_low + 1e-10)
        # 成交量相对50日均值
        vol_ma50 = volume.rolling(50).mean()
        vol_ratio = volume / (vol_ma50 + 1e-10)
        # 突破信号：价格接近上轨或下轨且成交量大于均值1.5倍
        breakout_upper = (price_pos > 0.9) & (vol_ratio > 1.5)
        breakout_lower = (price_pos < 0.1) & (vol_ratio > 1.5)
        # 合成信号：上突破为正，下突破为负
        raw = breakout_upper.astype(float) - breakout_lower.astype(float)
        # 用vol_ratio平滑，防止突变，并限幅
        result = raw * np.minimum(vol_ratio / 2.0, 1.0)
        result = result.fillna(0).clip(-1, 1)
        return result
