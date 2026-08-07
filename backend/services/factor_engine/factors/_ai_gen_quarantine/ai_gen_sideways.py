"""AI因子: 横盘震荡风险因子 | 置信:60% | 当价格长期围绕移动平均线窄幅震荡，没有明显趋势时，容易导致方向错误和持仓超时亏损。因子值接近-1表示市场处于横盘震荡高危险状态，接近+1表示趋势明确。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class SidewaysMarketRiskFactor(BaseFactor):
    """当价格长期围绕移动平均线窄幅震荡，没有明显趋势时，容易导致方向错误和持仓超时亏损。因子值接近-1表示市场处于横盘震荡高危险状态，接近+1表示趋势明确。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sideways",
            name="Sideways Market Risk Factor",
            display_name="横盘震荡风险因子",
            description="当价格长期围绕移动平均线窄幅震荡，没有明显趋势时，容易导致方向错误和持仓超时亏损。因子值接近-1表示市场处于横盘震荡高危险状态，接近+1表示趋势明确。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # 计算20日均线
        ma20 = df['close'].rolling(window=20, min_periods=1).mean()
        # 价格偏离均线的百分比
        deviation = (df['close'] - ma20) / (ma20 + 1e-10)
        # 计算过去20日偏离绝对值的平均值（即波动幅度）
        abs_dev = deviation.abs()
        avg_abs_dev = abs_dev.rolling(window=20, min_periods=1).mean()
        # 当前偏离绝对值相对于平均偏离幅度的比值
        dev_ratio = abs_dev / (avg_abs_dev + 1e-10)
        # 当偏离很小且比值低于阈值时，说明价格在均线附近徘徊，趋势不明
        # 同时计算价格是否在近期高低点中间区域（窄幅震荡）
        recent_high = df['high'].rolling(window=10, min_periods=1).max()
        recent_low = df['low'].rolling(window=10, min_periods=1).min()
        range_width = (recent_high - recent_low) / (df['close'] + 1e-10)
        avg_range = range_width.rolling(window=20, min_periods=1).mean()
        range_ratio = range_width / (avg_range + 1e-10)
        # 综合判断：偏离小且波动范围窄时，为横盘
        sideways = ((dev_ratio < 0.8) & (range_ratio < 0.8)).astype(float)
        # 映射为趋势强度：正值为趋势向上，负值为趋势向下，但风险是横盘->0附近
        # 使用偏离方向并乘以强度
        strength = 1.0 - sideways  # 0表示横盘，1表示有趋势
        direction = np.sign(deviation)  # 正负方向
        result = direction * strength
        # 填充NaN
        result = result.fillna(0.0)
        return result
