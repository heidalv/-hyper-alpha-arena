"""AI因子: 趋势清晰度得分 | 置信:70% | 基于ADX思想和线性回归斜率，评估当前趋势的清晰度和持续性。当趋势模糊（类似regime unknown）时，因子偏向负值，提示谨慎。计算：计算20期线性回归斜率及其R平方，结合方向性。斜率绝对值大且R平方高表明趋势清晰，输出正值；否则负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendClarityScore(BaseFactor):
    """基于ADX思想和线性回归斜率，评估当前趋势的清晰度和持续性。当趋势模糊（类似regime unknown）时，因子偏向负值，提示谨慎。计算：计算20期线性回归斜率及其R平方，结合方向性。斜率绝对值大且R平方高表明趋势清晰，输出正值；否则负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tscore",
            name="Trend Clarity Score",
            display_name="趋势清晰度得分",
            description="基于ADX思想和线性回归斜率，评估当前趋势的清晰度和持续性。当趋势模糊（类似regime unknown）时，因子偏向负值，提示谨慎。计算：计算20期线性回归斜率及其R平方，结合方向性。斜率绝对值大且R平方高表明趋势清晰，输出正值；否则负值。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        if len(data) < 20:
            return pd.Series(0, index=data.index)
        close = data['close']
        # 滚动线性回归
        def regress(series):
            x = np.arange(len(series))
            y = series.values
            if len(y) < 2 or np.std(y)==0:
                return 0, 0
            slope, intercept = np.polyfit(x, y, 1)
            y_pred = slope * x + intercept
            ss_res = np.sum((y - y_pred)**2)
            ss_tot = np.sum((y - np.mean(y))**2)
            r2 = 1 - ss_res/(ss_tot+1e-10)
            return slope, r2
        # 应用滚动窗口
        slopes = close.rolling(20).apply(lambda s: regress(s)[0], raw=False)
        rsq = close.rolling(20).apply(lambda s: regress(s)[1], raw=False)
        # 标准化斜率（用close均值缩放）
        mean_close = close.rolling(20).mean()
        norm_slope = slopes / (mean_close + 1e-10) * 100  # 百分比斜率
        # 清晰度得分 = 斜率符号 * r2 的平方
        clarity = np.sign(norm_slope) * (rsq ** 2)
        # 映射到[-1,1]：clarity范围约为[-0.2,0.2] 放大
        factor = clarity * 5
        factor = factor.clip(-1, 1)
        factor = factor.fillna(0)
        return factor
