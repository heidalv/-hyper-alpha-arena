"""AI因子: 归一化波动率状态 | 置信:65% | 计算过去20周期波动率（标准差）与过去100周期波动率中位数的比值，然后归一化到[-1,1]。比值小于1表示当前波动率偏低，市场可能处于窄幅震荡，止损容易被触发，因子值偏负；比值大于1表示波动偏高，因子值偏正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NormalizedVolatilityRegime(BaseFactor):
    """计算过去20周期波动率（标准差）与过去100周期波动率中位数的比值，然后归一化到[-1,1]。比值小于1表示当前波动率偏低，市场可能处于窄幅震荡，止损容易被触发，因子值偏负；比值大于1表示波动偏高，因子值偏正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol",
            name="Normalized Volatility Regime",
            display_name="归一化波动率状态",
            description="计算过去20周期波动率（标准差）与过去100周期波动率中位数的比值，然后归一化到[-1,1]。比值小于1表示当前波动率偏低，市场可能处于窄幅震荡，止损容易被触发，因子值偏负；比值大于1表示波动偏高，因子值偏正。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns open, high, low, close, volume
        # 计算日内真实波幅 ATR-like 但使用收盘价变动
        close = data['close']
        returns = close.pct_change()
        # 20期波动率（标准差）
        vol20 = returns.rolling(20, min_periods=10).std()
        # 100期波动率中位数
        vol100_median = returns.rolling(100, min_periods=50).median()
        # 比值，避免除零
        ratio = vol20 / (vol100_median + 1e-10)
        # 截断到[0.1, 10]并映射到[-1,1]
        ratio_clipped = np.clip(ratio, 0.1, 10.0)
        result = 2 * (np.log10(ratio_clipped) - np.log10(0.1)) / (np.log10(10) - np.log10(0.1)) - 1
        # 填充NaN
        result = result.fillna(0.0)
        return pd.Series(result, index=data.index, name='ai_gen_vol')
