"""AI因子: 趋势强度得分 | 置信:70% | 基于近期收盘价线性回归斜率与R方的乘积，衡量趋势的确定性与强度。正值表示上升趋势，负值表示下降趋势，值接近0表示震荡。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthScore(BaseFactor):
    """基于近期收盘价线性回归斜率与R方的乘积，衡量趋势的确定性与强度。正值表示上升趋势，负值表示下降趋势，值接近0表示震荡。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tss",
            name="TrendStrengthScore",
            display_name="趋势强度得分",
            description="基于近期收盘价线性回归斜率与R方的乘积，衡量趋势的确定性与强度。正值表示上升趋势，负值表示下降趋势，值接近0表示震荡。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        window = 14
        def trend_score(series):
            x = np.arange(len(series))
            if len(series) < 2:
                return 0.0
            slope, intercept = np.polyfit(x, series, 1)
            # 计算R方
            y_pred = slope * x + intercept
            ss_res = np.sum((series - y_pred) ** 2)
            ss_tot = np.sum((series - np.mean(series)) ** 2)
            r2 = 1 - ss_res / ss_tot if ss_tot != 0 else 0
            # 归一化斜率（相对于均值的百分比变化）
            mean_price = np.mean(series)
            norm_slope = slope / mean_price * 100 if mean_price != 0 else 0
            score = norm_slope * r2
            # 限制到[-1,1]：假设最大斜率变化为10%
            return np.clip(score / 10.0, -1, 1)
        result = close.rolling(window, min_periods=2).apply(trend_score, raw=True)
        return result
