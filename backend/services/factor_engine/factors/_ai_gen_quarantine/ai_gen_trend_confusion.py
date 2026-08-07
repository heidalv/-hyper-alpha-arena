"""AI因子: 趋势模糊不确定性因子 | 置信:60% | 度量价格在均线附近反复穿越的程度，反映趋势不明确、容易触发超时或强行平仓的情形。通过计算价格与短期均线偏离度及均线斜率的变化率来量化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConfusionUncertainty(BaseFactor):
    """度量价格在均线附近反复穿越的程度，反映趋势不明确、容易触发超时或强行平仓的情形。通过计算价格与短期均线偏离度及均线斜率的变化率来量化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_confusion",
            name="Trend Confusion Uncertainty",
            display_name="趋势模糊不确定性因子",
            description="度量价格在均线附近反复穿越的程度，反映趋势不明确、容易触发超时或强行平仓的情形。通过计算价格与短期均线偏离度及均线斜率的变化率来量化。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 短期均线（5日）和长期均线（20日）
        ma5 = close.rolling(5, min_periods=1).mean()
        ma20 = close.rolling(20, min_periods=1).mean()
        # 价格与ma5的偏离度
        dev = (close - ma5) / ma5
        # ma5的斜率变化率（一阶差分）
        slope = ma5.diff() / ma5.shift(1).clip(lower=1e-6)
        # 斜率变化的标准差（短期波动）
        slope_vol = slope.rolling(5, min_periods=1).std()
        # 当价格偏离小且斜率波动大时，趋势模糊
        # 合并两个信号：低偏离（绝对值小）和高斜率波动
        low_dev = 1 - np.abs(dev).clip(upper=1)
        high_slope_vol = slope_vol / slope_vol.rolling(20, min_periods=1).mean().clip(lower=1e-6)
        score = low_dev * high_slope_vol
        # 归一化到[-1,1]，使用tanh缩放
        result = np.tanh(score - 0.5)  # 中心化
        return result
