"""AI因子: 反转强度因子 | 置信:60% | 基于价格与成交量关系，检测短期反转强度。计算过去N周期内收盘价相对于开盘价的变动方向与成交量变化率的乘积，归一化到[-1,1]。正值表示放量上涨后可能反转下跌，负值表示放量下跌后可能反转上涨。适用于捕捉dust_cleanup和ai_reverse模式下的反转亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalStrength(BaseFactor):
    """基于价格与成交量关系，检测短期反转强度。计算过去N周期内收盘价相对于开盘价的变动方向与成交量变化率的乘积，归一化到[-1,1]。正值表示放量上涨后可能反转下跌，负值表示放量下跌后可能反转上涨。适用于捕捉dust_cleanup和ai_reverse模式下的反转亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_revstrength",
            name="Reversal Strength",
            display_name="反转强度因子",
            description="基于价格与成交量关系，检测短期反转强度。计算过去N周期内收盘价相对于开盘价的变动方向与成交量变化率的乘积，归一化到[-1,1]。正值表示放量上涨后可能反转下跌，负值表示放量下跌后可能反转上涨。适用于捕捉dust_cleanup和ai_reverse模式下的反转亏损。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns open, high, low, close, volume
        window = 5
        ret = (data['close'] - data['open']) / data['open']
        vol_change = data['volume'].pct_change()
        raw = ret * vol_change
        # 滚动标准化
        mean = raw.rolling(window).mean()
        std = raw.rolling(window).std()
        z = (raw - mean) / (std + 1e-10)
        result = np.clip(z, -1, 1)
        return result
