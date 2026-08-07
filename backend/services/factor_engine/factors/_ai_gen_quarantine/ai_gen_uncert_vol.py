"""AI因子: 不确定性波动率尖峰 | 置信:50% | 基于日内价格波动与近期历史波动率的比值，当比值异常高时表示市场状态未知，风险增大。计算过去20根K线的最高价与最低价范围的中位数，作为基准波动；当前K线日内波幅与基准波幅比值，经Z-score标准化后映射到[-1,+1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UncertaintyVolatilitySpike(BaseFactor):
    """基于日内价格波动与近期历史波动率的比值，当比值异常高时表示市场状态未知，风险增大。计算过去20根K线的最高价与最低价范围的中位数，作为基准波动；当前K线日内波幅与基准波幅比值，经Z-score标准化后映射到[-1,+1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_uncert_vol",
            name="Uncertainty Volatility Spike",
            display_name="不确定性波动率尖峰",
            description="基于日内价格波动与近期历史波动率的比值，当比值异常高时表示市场状态未知，风险增大。计算过去20根K线的最高价与最低价范围的中位数，作为基准波动；当前K线日内波幅与基准波幅比值，经Z-score标准化后映射到[-1,+1]。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns open, high, low, close, volume
        import numpy as np
        # 日内波幅
        daily_range = data['high'] - data['low']
        # 过去20根K线的中位数波幅（滚动）
        median_range = daily_range.rolling(window=20, min_periods=5).median().fillna(method='bfill').fillna(daily_range)
        ratio = daily_range / median_range
        # Z-score
        z = (ratio - ratio.rolling(50, min_periods=10).mean()) / ratio.rolling(50, min_periods=10).std()
        z = z.fillna(0).clip(-3, 3)  # clip outliers
        # 映射到[-1,1]，正值为高不确定性
        result = np.tanh(z / 2.0)
        return result
