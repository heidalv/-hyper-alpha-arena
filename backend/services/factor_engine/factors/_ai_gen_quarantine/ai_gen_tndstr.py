"""AI因子: 趋势强度因子 | 置信:65% | 通过线性回归拟合近期价格，计算R平方值衡量趋势清晰度。R平方低（<0.3）表示趋势不明朗，做多风险高，因子为负；R平方高则为正。适用于过滤regime=unknown时的多头开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrength(BaseFactor):
    """通过线性回归拟合近期价格，计算R平方值衡量趋势清晰度。R平方低（<0.3）表示趋势不明朗，做多风险高，因子为负；R平方高则为正。适用于过滤regime=unknown时的多头开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tndstr",
            name="TrendStrength",
            display_name="趋势强度因子",
            description="通过线性回归拟合近期价格，计算R平方值衡量趋势清晰度。R平方低（<0.3）表示趋势不明朗，做多风险高，因子为负；R平方高则为正。适用于过滤regime=unknown时的多头开仓。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 使用过去20根K线
        window = 20
        close = data['close']
        if len(close) < window:
            return pd.Series(0.0, index=close.index)
        # 计算滚动线性回归的R平方
        def r_squared(series):
            import numpy as np
            y = series.values
            x = np.arange(len(y))
            slope, intercept = np.polyfit(x, y, 1)
            y_pred = slope * x + intercept
            ss_res = np.sum((y - y_pred)**2)
            ss_tot = np.sum((y - np.mean(y))**2)
            return ss_res / ss_tot if ss_tot != 0 else 0
        r2 = close.rolling(window).apply(r_squared, raw=False)
        # 映射到[-1,1]: R2<0.3 -> -1, 0.3-0.7线性插值, >0.7 -> 1
        lower = 0.3
        upper = 0.7
        factor = -1 + 2 * (r2 - lower) / (upper - lower)
        factor = factor.clip(-1, 1)
        return factor
