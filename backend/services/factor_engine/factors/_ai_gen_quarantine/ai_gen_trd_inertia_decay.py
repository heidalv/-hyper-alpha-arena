"""AI因子: 趋势惯性衰减 | 置信:60% | 通过计算过去N根K线的线性回归斜率与判定系数的乘积，衡量趋势的强度与稳定性。当趋势强且稳定时值为正，当趋势弱或波动大时值为负，从而避免在趋势不明时持仓过久。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Inertia_Decay(BaseFactor):
    """通过计算过去N根K线的线性回归斜率与判定系数的乘积，衡量趋势的强度与稳定性。当趋势强且稳定时值为正，当趋势弱或波动大时值为负，从而避免在趋势不明时持仓过久。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trd_inertia_decay",
            name="Trend Inertia Decay",
            display_name="趋势惯性衰减",
            description="通过计算过去N根K线的线性回归斜率与判定系数的乘积，衡量趋势的强度与稳定性。当趋势强且稳定时值为正，当趋势弱或波动大时值为负，从而避免在趋势不明时持仓过久。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 20
        close = data['close']
        volume = data['volume']
        # 计算对数价格
        log_price = np.log(close)
        # 滚动线性回归斜率（最小二乘法）
        def linreg_slope_r2(series):
            x = np.arange(len(series))
            y = series.values
            if len(y) < 2:
                return np.nan, np.nan
            A = np.vstack([x, np.ones(len(x))]).T
            slope, intercept = np.linalg.lstsq(A, y, rcond=None)[0]
            y_pred = slope * x + intercept
            ss_res = np.sum((y - y_pred)**2)
            ss_tot = np.sum((y - np.mean(y))**2)
            r2 = 1 - ss_res / ss_tot if ss_tot != 0 else 0
            return slope, r2
        # 滚动计算
        slope = close.rolling(n).apply(lambda s: linreg_slope_r2(s)[0], raw=False)
        r2 = close.rolling(n).apply(lambda s: linreg_slope_r2(s)[1], raw=False)
        # 标准化斜率（乘以100便于计算）
        slope_norm = slope * 100
        # 惯性强度 = 斜率 * R^2，然后压缩到[-1,1]
        inertia = slope_norm * r2
        # 使用tanh压缩
        result = pd.Series(np.tanh(inertia), index=data.index)
        # 用前向填充处理NaN
        result = result.fillna(0)
        return result
