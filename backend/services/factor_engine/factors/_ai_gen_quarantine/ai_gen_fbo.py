"""AI因子: 假突破指标 | 置信:70% | 检测价格突破近期高点但成交量萎缩的情况。计算当前收盘价是否超过过去20日最高价，同时成交量低于过去20日均量。若两者同时满足则为假突破信号，输出负值（不利做多）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Fake_Breakout_Indicator(BaseFactor):
    """检测价格突破近期高点但成交量萎缩的情况。计算当前收盘价是否超过过去20日最高价，同时成交量低于过去20日均量。若两者同时满足则为假突破信号，输出负值（不利做多）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fbo",
            name="Fake Breakout Indicator",
            display_name="假突破指标",
            description="检测价格突破近期高点但成交量萎缩的情况。计算当前收盘价是否超过过去20日最高价，同时成交量低于过去20日均量。若两者同时满足则为假突破信号，输出负值（不利做多）。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 过去20日最高价
        high_20 = data['high'].rolling(20).max().shift(1)
        # 突破阈值：当前收盘价超过过去高点
        break_up = (data['close'] > high_20).astype(float)
        # 成交量低于过去20日均量
        vol_ma = data['volume'].rolling(20).mean().shift(1)
        low_vol = (data['volume'] < vol_ma * 0.8).astype(float)
        # 假突破信号
        fake = break_up * low_vol
        # 用价格偏离度加权
        price_dev = (data['close'] - high_20) / (data['close'] + 1e-10)
        factor = -fake * np.clip(price_dev * 10, 0, 1)
        factor = np.clip(factor, -1, 1)
        return factor
