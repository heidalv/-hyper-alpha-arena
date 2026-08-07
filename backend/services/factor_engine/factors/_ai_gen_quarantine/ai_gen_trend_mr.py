"""AI因子: 趋势-均值回归复合因子 | 置信:50% | 结合短期趋势强度与价格偏离均值的程度。当短期趋势弱（R<0.3）且价格偏离20日均线超过2%时，市场方向不明易触发假突破或反转亏损。因子输出负值表示高风险，正值表示趋势明确或均值回归安全。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendMeanreversionComposite(BaseFactor):
    """结合短期趋势强度与价格偏离均值的程度。当短期趋势弱（R<0.3）且价格偏离20日均线超过2%时，市场方向不明易触发假突破或反转亏损。因子输出负值表示高风险，正值表示趋势明确或均值回归安全。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_mr",
            name="Trend-MeanReversion Composite",
            display_name="趋势-均值回归复合因子",
            description="结合短期趋势强度与价格偏离均值的程度。当短期趋势弱（R<0.3）且价格偏离20日均线超过2%时，市场方向不明易触发假突破或反转亏损。因子输出负值表示高风险，正值表示趋势明确或均值回归安全。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算20日均线
        ma20 = data['close'].rolling(20).mean()
        # 价格偏离度
        deviation = (data['close'] - ma20) / ma20
        # 短期趋势强度：用5日线性回归斜率归一化
        slope = data['close'].rolling(5).apply(lambda x: np.polyfit(range(5), x, 1)[0], raw=True)
        slope_norm = slope / data['close'].rolling(5).mean()  # 相对斜率
        # 趋势弱：斜率绝对值小
        weak_trend = 1 - np.tanh(np.abs(slope_norm) * 100)
        # 偏离大：绝对值大
        large_dev = np.abs(deviation)
        # 风险信号：趋势弱且偏离大
        risk = weak_trend * (large_dev - 0.02).clip(0, 0.05) / 0.05
        # 最终输出负值表示高风险
        result = -np.tanh(risk * 20)
        result = result.fillna(0).clip(-1, 1)
        return result
