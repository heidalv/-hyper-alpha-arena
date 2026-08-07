"""AI因子: 布林带成交量假突破 | 置信:60% | 当价格触及布林带上轨且成交量低于20日均量时，视为假突破信号，给出负值（做空或避免做多）。使用当前价格与上轨的偏离度乘以成交量衰减程度，归一化至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BB_Volume_Fakeout(BaseFactor):
    """当价格触及布林带上轨且成交量低于20日均量时，视为假突破信号，给出负值（做空或避免做多）。使用当前价格与上轨的偏离度乘以成交量衰减程度，归一化至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bbvol",
            name="BB_Volume_Fakeout",
            display_name="布林带成交量假突破",
            description="当价格触及布林带上轨且成交量低于20日均量时，视为假突破信号，给出负值（做空或避免做多）。使用当前价格与上轨的偏离度乘以成交量衰减程度，归一化至[-1,1]。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 滚动平均
        length = 20
        if len(data) < length:
            return pd.Series(np.nan, index=data.index)
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 布林带
        sma = close.rolling(length).mean()
        std = close.rolling(length).std()
        upper = sma + 2 * std
        # 价格相对于上轨的偏离（正为突破）
        deviation = (close - upper) / (upper + 1e-10)  # 防止除零
        # 成交量比率
        vol_ma = volume.rolling(length).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 假突破信号：突破上轨但成交量萎缩（ratio<1）
        # 组合：偏离度越大且成交量越萎缩，越负
        signal = deviation * (1 - vol_ratio)  # 当vol_ratio<1时为正贡献（信号负）
        # 限制极端值并归一化到[-1,1]
        signal = signal.clip(-1, 1)
        # 填充NaN
        signal = signal.fillna(0)
        return signal
