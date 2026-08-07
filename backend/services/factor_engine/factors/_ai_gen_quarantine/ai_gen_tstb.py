"""AI因子: 趋势稳定性 | 置信:60% | 通过计算过去N周期线性回归的R²和斜率稳定性，衡量当前趋势的可靠性。低R²或斜率波动大时表明趋势不稳定，值接近-1；稳定趋势接近+1。旨在过滤掉regime=unknown下的假趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStability(BaseFactor):
    """通过计算过去N周期线性回归的R²和斜率稳定性，衡量当前趋势的可靠性。低R²或斜率波动大时表明趋势不稳定，值接近-1；稳定趋势接近+1。旨在过滤掉regime=unknown下的假趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tstb",
            name="TrendStability",
            display_name="趋势稳定性",
            description="通过计算过去N周期线性回归的R²和斜率稳定性，衡量当前趋势的可靠性。低R²或斜率波动大时表明趋势不稳定，值接近-1；稳定趋势接近+1。旨在过滤掉regime=unknown下的假趋势。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        n = 20
        def stability(series):
            x = np.arange(len(series))
            slope, intercept = np.polyfit(x, series, 1)
            pred = slope * x + intercept
            residuals = series - pred
            ss_res = np.sum(residuals**2)
            ss_tot = np.sum((series - np.mean(series))**2)
            r2 = 1 - ss_res / (ss_tot + 1e-10)
            return r2
        rolling_r2 = close.rolling(n).apply(stability, raw=False)
        mean_r2 = rolling_r2.rolling(10).mean()
        std_r2 = rolling_r2.rolling(10).std()
        result = (mean_r2 - 0.5) * 2  # 映射到[-1,1]
        result = result.clip(-1, 1)
        return result
