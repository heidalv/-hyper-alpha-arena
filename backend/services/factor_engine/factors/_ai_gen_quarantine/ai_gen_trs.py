"""AI因子: 趋势清晰度指标 | 置信:70% | 基于过去20期收盘价的线性回归拟合优度（R²）。R²越高说明价格运动越接近直线趋势，市场状态明确；R²越低说明价格杂乱无章，处于regime unknown状态。将R²线性映射到[-1,1]，低R²对应负值，提示避免单向做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Sharpness_Indicator(BaseFactor):
    """基于过去20期收盘价的线性回归拟合优度（R²）。R²越高说明价格运动越接近直线趋势，市场状态明确；R²越低说明价格杂乱无章，处于regime unknown状态。将R²线性映射到[-1,1]，低R²对应负值，提示避免单向做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trs",
            name="Trend Sharpness Indicator",
            display_name="趋势清晰度指标",
            description="基于过去20期收盘价的线性回归拟合优度（R²）。R²越高说明价格运动越接近直线趋势，市场状态明确；R²越低说明价格杂乱无章，处于regime unknown状态。将R²线性映射到[-1,1]，低R²对应负值，提示避免单向做多。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        n = 20
        def r2(series):
            x = np.arange(len(series))
            y = series.values
            if len(y) < 2:
                return 0.5
            corr = np.corrcoef(x, y)[0, 1]
            return corr * corr  # R²
        result = close.rolling(n).apply(r2, raw=False)
        # 映射R²从[0,1]到[-1,1]
        result = result * 2 - 1
        return result.fillna(0).clip(-1, 1)
