"""AI因子: 市场状态不确定性评分 | 置信:65% | 通过短期波动率与长期趋势强度的比值，识别市场是否处于趋势不明朗的高波动状态（regime=unknown）。当波动率扩大但趋势强度减弱时，值为负，建议避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Uncertainty_Score(BaseFactor):
    """通过短期波动率与长期趋势强度的比值，识别市场是否处于趋势不明朗的高波动状态（regime=unknown）。当波动率扩大但趋势强度减弱时，值为负，建议避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_vol_trend",
            name="Regime Uncertainty Score",
            display_name="市场状态不确定性评分",
            description="通过短期波动率与长期趋势强度的比值，识别市场是否处于趋势不明朗的高波动状态（regime=unknown）。当波动率扩大但趋势强度减弱时，值为负，建议避免做多。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 波动率：过去20日最高最低振幅
        vol = (high.rolling(20).max() - low.rolling(20).min()) / close.rolling(20).mean()
        # 趋势强度：过去20日收盘价线性回归斜率归一化
        import numpy as np
        def trend_slope(series):
            y = series.values
            x = np.arange(len(y))
            if len(y) < 2: return np.nan
            slope = np.polyfit(x, y, 1)[0]
            return slope / y.mean() if y.mean() != 0 else 0
        trend = close.rolling(20).apply(trend_slope, raw=False)
        # 波动率与趋势强度比值，取绝对值再反向
        ratio = vol / (abs(trend) + 1e-8)
        # 标准化为[-1,1]，高比值表示不确定性高，做多风险大
        result = -np.tanh(ratio - np.nanmedian(ratio))
        return result
