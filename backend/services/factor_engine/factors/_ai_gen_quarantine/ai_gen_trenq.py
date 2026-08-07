"""AI因子: 趋势质量 | 置信:70% | 评估价格趋势的清晰度。通过对过去N日收盘价做线性回归，计算斜率与残差标准差的比值，比值越低说明趋势越弱、噪音越大，此时趋势跟踪策略易亏损。输出-1到+1，负值表示趋势质量差。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendQuality(BaseFactor):
    """评估价格趋势的清晰度。通过对过去N日收盘价做线性回归，计算斜率与残差标准差的比值，比值越低说明趋势越弱、噪音越大，此时趋势跟踪策略易亏损。输出-1到+1，负值表示趋势质量差。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trenq",
            name="TrendQuality",
            display_name="趋势质量",
            description="评估价格趋势的清晰度。通过对过去N日收盘价做线性回归，计算斜率与残差标准差的比值，比值越低说明趋势越弱、噪音越大，此时趋势跟踪策略易亏损。输出-1到+1，负值表示趋势质量差。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close'].values
        n = 20
        def trend_quality(series):
            x = np.arange(len(series))
            if len(series) < n:
                return np.nan
            slope, intercept = np.polyfit(x, series, 1)
            residuals = series - (slope * x + intercept)
            std_resid = np.std(residuals)
            if std_resid == 0:
                return 0
            # 信号强度：斜率绝对值与噪声之比，但需要归一化
            quality = slope / std_resid
            # 使用tanh压缩到[-1,1]附近
            return np.tanh(quality * 10)
        result = pd.Series(index=data.index, dtype=float)
        for i in range(n-1, len(data)):
            result.iloc[i] = trend_quality(close[i-n+1:i+1])
        result = result.fillna(0)
        # 转为-1到1，负值表示趋势不清晰
        return result
