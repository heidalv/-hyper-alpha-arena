"""AI因子: 趋势一致性评分 | 置信:70% | 计算短期均线与长期均线之间的相对位置和斜率一致性，判断趋势是否清晰。当短期均线在长期均线上且两者斜率同向时，趋势明确为正值；当交叉或背离时，表示趋势不明，返回负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Consistency_Score(BaseFactor):
    """计算短期均线与长期均线之间的相对位置和斜率一致性，判断趋势是否清晰。当短期均线在长期均线上且两者斜率同向时，趋势明确为正值；当交叉或背离时，表示趋势不明，返回负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendcons",
            name="Trend Consistency Score",
            display_name="趋势一致性评分",
            description="计算短期均线与长期均线之间的相对位置和斜率一致性，判断趋势是否清晰。当短期均线在长期均线上且两者斜率同向时，趋势明确为正值；当交叉或背离时，表示趋势不明，返回负值。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma_short = close.rolling(10).mean()
        ma_long = close.rolling(30).mean()
        # 斜率（1日差分）
        slope_short = ma_short.diff(3) / ma_short.shift(3)
        slope_long = ma_long.diff(3) / ma_long.shift(3)
        # 位置：短均线相对长均线
        position = (ma_short - ma_long) / (ma_long + 1e-10)
        # 方向一致性：短斜率和长斜率同号
        same_sign = (np.sign(slope_short) == np.sign(slope_long)).astype(float)
        # 强度：位置绝对值越大，信号越强
        intensity = position.abs().clip(0, 0.1) / 0.1  # 归一化
        # 综合：趋势明确时为正，不明确为负
        signal = (same_sign * intensity * 2 - 1)  # 映射到[-1,1]
        # 当趋势反转时（position穿过0）惩罚
        cross = ((position > 0) & (position.shift() <= 0)) | ((position < 0) & (position.shift() >= 0))
        signal = signal.where(~cross, -1.0)
        # 平滑
        result = signal.rolling(3, min_periods=1).mean().fillna(0)
        return result.clip(-1.0, 1.0)
