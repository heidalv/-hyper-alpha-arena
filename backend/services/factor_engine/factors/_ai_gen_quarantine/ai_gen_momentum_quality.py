"""AI因子: 动量质量 | 置信:60% | 衡量趋势的稳定性和持续性，避免在无趋势震荡市中持仓。结合价格线性回归斜率、残差波动率、以及价格路径效率比，高值表示高质量趋势，低值表示震荡或噪音。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumQuality(BaseFactor):
    """衡量趋势的稳定性和持续性，避免在无趋势震荡市中持仓。结合价格线性回归斜率、残差波动率、以及价格路径效率比，高值表示高质量趋势，低值表示震荡或噪音。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_quality",
            name="Momentum_Quality",
            display_name="动量质量",
            description="衡量趋势的稳定性和持续性，避免在无趋势震荡市中持仓。结合价格线性回归斜率、残差波动率、以及价格路径效率比，高值表示高质量趋势，低值表示震荡或噪音。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 线性回归斜率（过去20日）
        def rolling_slope(series, window):
            x = np.arange(window)
            y = series.values
            # 使用向量化计算协方差
            def slope(arr):
                if np.isnan(arr).any():
                    return np.nan
                return np.polyfit(x, arr, 1)[0]
            return series.rolling(window).apply(slope, raw=True)
        slope = rolling_slope(close, 20)
        # 残差波动率：实际价格与回归直线偏离的标准差
        def rolling_r2(series, window):
            x = np.arange(window)
            def r2(arr):
                if np.isnan(arr).any():
                    return np.nan
                coeff = np.polyfit(x, arr, 1)
                fitted = np.polyval(coeff, x)
                ss_res = np.sum((arr - fitted)**2)
                ss_tot = np.sum((arr - np.mean(arr))**2)
                return 1 - (ss_res / (ss_tot + 1e-10))
            return series.rolling(window).apply(r2, raw=True)
        r_squared = rolling_r2(close, 20)
        # 路径效率比：收盘价净变化 / 累积真实波幅
        cum_change = close - close.shift(20)
        true_range = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        cum_bounce = true_range.rolling(20).sum()
        efficiency = np.abs(cum_change) / (cum_bounce + 1e-10)
        # 组合：线性斜率方向乘以R方和效率的乘积
        slope_sign = np.sign(slope)
        quality = slope_sign * r_squared * efficiency
        # 归一化到[-1,1]
        result = quality.rolling(60).apply(lambda x: np.clip(x, -1, 1), raw=True)
        result = result.fillna(0)
        return result
