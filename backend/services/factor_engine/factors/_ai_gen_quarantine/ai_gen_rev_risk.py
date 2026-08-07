"""AI因子: AI反转风险 | 置信:60% | 检测短期价格出现冲高回落或假突破特征，预示即将反转下跌。通过计算日内价格动量、成交量确认以及上下影线比例，当出现上涨但成交量萎缩或长上影线时输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AI_Reverse_Risk(BaseFactor):
    """检测短期价格出现冲高回落或假突破特征，预示即将反转下跌。通过计算日内价格动量、成交量确认以及上下影线比例，当出现上涨但成交量萎缩或长上影线时输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_risk",
            name="AI Reverse Risk",
            display_name="AI反转风险",
            description="检测短期价格出现冲高回落或假突破特征，预示即将反转下跌。通过计算日内价格动量、成交量确认以及上下影线比例，当出现上涨但成交量萎缩或长上影线时输出负值。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        open_price = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']

        # 日内价格变化
        range_pct = (high - low) / open_price * 100
        body = abs(close - open_price)
        upper_shadow = high - np.maximum(close, open_price)
        lower_shadow = np.minimum(close, open_price) - low
        shadow_ratio = upper_shadow / (range_pct * open_price / 100 + 1e-8)

        # 上涨且上影线长 => 反转信号
        up_candle = close > open_price
        long_upper = (upper_shadow > body * 2) & (upper_shadow > lower_shadow * 2)
        # 成交量萎缩：今日量小于前5日均量
        avg_vol_5 = volume.rolling(5).mean()
        vol_shrink = volume < avg_vol_5 * 0.8

        # 动量：过去3日涨幅
        ret_3 = close.pct_change(3)
        momentum = ret_3 > 0.02

        signal = up_candle & long_upper & vol_shrink & momentum
        result = signal.astype(float) * -1.0
        # 平滑处理并填充
        result = result.rolling(3).mean().fillna(0).clip(-1, 0)
        return result
