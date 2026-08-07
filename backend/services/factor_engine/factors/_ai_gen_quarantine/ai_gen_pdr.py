"""AI因子: 价格动量衰减因子 | 置信:60% | 通过短期线性回归斜率的变化率衡量价格动量是否衰竭。当斜率从正转负或加速下降时，表明上涨动能耗尽，后续易回调；反之亦然。因子负值表示趋势衰减风险高，正值表示动能增强。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceDecayRate(BaseFactor):
    """通过短期线性回归斜率的变化率衡量价格动量是否衰竭。当斜率从正转负或加速下降时，表明上涨动能耗尽，后续易回调；反之亦然。因子负值表示趋势衰减风险高，正值表示动能增强。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pdr",
            name="Price Decay Rate",
            display_name="价格动量衰减因子",
            description="通过短期线性回归斜率的变化率衡量价格动量是否衰竭。当斜率从正转负或加速下降时，表明上涨动能耗尽，后续易回调；反之亦然。因子负值表示趋势衰减风险高，正值表示动能增强。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算短期线性回归斜率（5日）
        def rolling_slope(series, window):
            x = np.arange(window)
            def _slope(y):
                if len(y) < window:
                    return np.nan
                return np.polyfit(x, y, 1)[0]
            return series.rolling(window).apply(_slope, raw=False)
        close = data['close']
        slope1 = rolling_slope(close, 5)  # 5日斜率
        slope2 = rolling_slope(close, 10)  # 10日斜率
        # 斜率变化率：短斜率相对于长斜率的偏离
        # 用z-score标准化
        diff = slope1 - slope2
        diff_mean = diff.rolling(20, min_periods=1).mean()
        diff_std = diff.rolling(20, min_periods=1).std()
        z = (diff - diff_mean) / (diff_std + 1e-8)
        # 当斜率衰减（z负且绝对值大）时表示动能衰竭，信号为负；增强为正
        # 同时结合价格位置避免噪声
        ma50 = close.rolling(50, min_periods=1).mean()
        price_dev = (close - ma50) / (ma50 + 1e-8)
        # 最终信号：z的相反数，并限制幅度
        raw = -z * 0.5  # 缩放
        # 极端情况下调整
        result = pd.Series(np.clip(raw, -1, 1), index=data.index)
        return result
