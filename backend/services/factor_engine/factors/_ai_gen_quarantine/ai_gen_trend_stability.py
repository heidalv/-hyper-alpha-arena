"""AI因子: 趋势稳定性因子 | 置信:65% | 衡量价格趋势的持续性和平滑度。使用过去N天的线性回归R²来衡量趋势的强度，同时结合价格与长期均线的偏离程度。当R²高且偏离适中时，趋势稳定，因子输出正值；当R²低或偏离过大时，趋势不稳定或反转风险高，输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Stability_Score(BaseFactor):
    """衡量价格趋势的持续性和平滑度。使用过去N天的线性回归R²来衡量趋势的强度，同时结合价格与长期均线的偏离程度。当R²高且偏离适中时，趋势稳定，因子输出正值；当R²低或偏离过大时，趋势不稳定或反转风险高，输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_stability",
            name="Trend Stability Score",
            display_name="趋势稳定性因子",
            description="衡量价格趋势的持续性和平滑度。使用过去N天的线性回归R²来衡量趋势的强度，同时结合价格与长期均线的偏离程度。当R²高且偏离适中时，趋势稳定，因子输出正值；当R²低或偏离过大时，趋势不稳定或反转风险高，输出负值。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        N = 20
        # 计算线性回归R²
        def rolling_r2(series, window):
            x = np.arange(window)
            def calc_r2(y):
                if len(y) < window: return np.nan
                slope, intercept = np.polyfit(x, y, 1)
                pred = slope * x + intercept
                ss_res = np.sum((y - pred)**2)
                ss_tot = np.sum((y - y.mean())**2)
                return 1 - ss_res / ss_tot if ss_tot != 0 else 0
            return series.rolling(window).apply(calc_r2, raw=True)
        r2 = rolling_r2(close, N)
        # 偏离度：当前价格与200均线的距离百分比
        ma200 = close.rolling(200).mean()
        deviation = (close - ma200) / ma200
        # 偏离度适中区间例如[-0.1, 0.1]内认为稳定，超出则不稳定
        dev_score = 1 - np.abs(deviation) / 0.2  # 归一化到[-1,1]左右
        dev_score = dev_score.clip(-1, 1)
        # R²作为趋势强度，映射到[0,1]
        r2_score = r2.fillna(0)
        # 综合：趋势稳定且偏离适中时正值，否则负值
        factor = (r2_score * 0.7 + (1 + dev_score)/2 * 0.3) * 2 - 1
        return factor.fillna(0).clip(-1,1)
