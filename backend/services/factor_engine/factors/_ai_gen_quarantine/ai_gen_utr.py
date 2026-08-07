"""AI因子: 未知趋势风险 | 置信:60% | 捕捉短期价格波动与长期均线背离且波动率异常的情况，对应'regime=unknown'下的亏损。计算短期动量（过去5日收益）与长期趋势（过去50日均线斜率）的差异，乘以相对波动率（当前ATR/均值ATR）放大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownTrendRisk(BaseFactor):
    """捕捉短期价格波动与长期均线背离且波动率异常的情况，对应'regime=unknown'下的亏损。计算短期动量（过去5日收益）与长期趋势（过去50日均线斜率）的差异，乘以相对波动率（当前ATR/均值ATR）放大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_utr",
            name="Unknown Trend Risk",
            display_name="未知趋势风险",
            description="捕捉短期价格波动与长期均线背离且波动率异常的情况，对应'regime=unknown'下的亏损。计算短期动量（过去5日收益）与长期趋势（过去50日均线斜率）的差异，乘以相对波动率（当前ATR/均值ATR）放大。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # short-term momentum
        short_ret = data['close'].pct_change(5)
        # long-term trend slope (linear regression over 50 days)
        close = data['close']
        def slope(series):
            if len(series) < 2:
                return 0.0
            x = np.arange(len(series))
            return np.polyfit(x, series, 1)[0]
        long_slope = close.rolling(50).apply(slope, raw=False)
        # normalize long slope by close
        long_trend = long_slope / (close + 1e-10) * 100
        # divergence
        divergence = short_ret - long_trend
        # volatility ratio: current ATR(14) / median ATR(50)
        atr = ((data['high'] - data['low']).rolling(14).mean())
        atr_median = atr.rolling(50).median()
        vol_ratio = atr / (atr_median + 1e-10)
        # high volatility + divergence -> uncertain regime
        signal = divergence * (vol_ratio - 1)
        # scale to [-1,1]
        result = signal.rolling(5).mean().fillna(0)
        result = result / (result.abs().max() + 1e-10)  # normalize
        return result.clip(-1, 1)
