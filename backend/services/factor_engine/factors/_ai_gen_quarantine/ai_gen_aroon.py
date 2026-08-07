"""AI因子: 阿隆趋势强度 | 置信:60% | 基于阿隆指标（Aroon Up - Aroon Down）衡量市场趋势的明确性。值越接近1表示强上升趋势，越接近-1表示强下降趋势，0附近表示震荡或无趋势。适用于识别regime=unknown的混沌环境，低绝对值时提示持仓风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AroonTrendStrength(BaseFactor):
    """基于阿隆指标（Aroon Up - Aroon Down）衡量市场趋势的明确性。值越接近1表示强上升趋势，越接近-1表示强下降趋势，0附近表示震荡或无趋势。适用于识别regime=unknown的混沌环境，低绝对值时提示持仓风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_aroon",
            name="Aroon Trend Strength",
            display_name="阿隆趋势强度",
            description="基于阿隆指标（Aroon Up - Aroon Down）衡量市场趋势的明确性。值越接近1表示强上升趋势，越接近-1表示强下降趋势，0附近表示震荡或无趋势。适用于识别regime=unknown的混沌环境，低绝对值时提示持仓风险。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 计算阿隆上行和下行
        period = 25
        high = data['high']
        low = data['low']
        # Aroon Up: 过去period日内最高价到当前的距离
        # 用rolling apply
        def aroon_up(ser):
            if len(ser) < period:
                return np.nan
            max_idx = ser.idxmax()
            days_since = (ser.index[-1] - max_idx).days
            return 100 * (period - days_since) / period
        def aroon_down(ser):
            if len(ser) < period:
                return np.nan
            min_idx = ser.idxmin()
            days_since = (ser.index[-1] - min_idx).days
            return 100 * (period - days_since) / period
        # 由于rolling不支持自定义索引差，改用rolling + shift的方法
        # 简便实现：用rolling window计算最大值位置
        # 或使用expanding
        # 标准化实现：使用20周期
        n = 20
        high_rolling = high.rolling(window=n, min_periods=n)
        low_rolling = low.rolling(window=n, min_periods=n)
        # 计算Aroon Up
        # 找每根K线向前n根内的最高价位置
        # 用rolling apply with np.argmax
        aroon_up_vals = high.rolling(window=n).apply(lambda x: (n - 1 - np.argmax(x)) / n * 100, raw=True)
        aroon_down_vals = low.rolling(window=n).apply(lambda x: (n - 1 - np.argmin(x)) / n * 100, raw=True)
        # 计算差值并归一化到[-1,1]
        aroon = (aroon_up_vals - aroon_down_vals) / 100.0
        return aroon.clip(-1, 1)
