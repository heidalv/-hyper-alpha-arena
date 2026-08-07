"""AI因子: 反转动量模式因子 | 置信:55% | 检测价格快速偏离均线后加速度转为负（多头）或正（空头）的潜在反转信号。当短期动量与长期均线偏离过大且加速度反转时，易发生亏损。返回-1表示高反转风险，+1表示趋势延续。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalPatternMomentum(BaseFactor):
    """检测价格快速偏离均线后加速度转为负（多头）或正（空头）的潜在反转信号。当短期动量与长期均线偏离过大且加速度反转时，易发生亏损。返回-1表示高反转风险，+1表示趋势延续。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rpm",
            name="ReversalPatternMomentum",
            display_name="反转动量模式因子",
            description="检测价格快速偏离均线后加速度转为负（多头）或正（空头）的潜在反转信号。当短期动量与长期均线偏离过大且加速度反转时，易发生亏损。返回-1表示高反转风险，+1表示趋势延续。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 短期和长期均线
        short_ma = close.rolling(5).mean()
        long_ma = close.rolling(20).mean()
        # 偏离度
        deviation = (close - long_ma) / long_ma.clip(lower=1e-10)
        # 动量加速度：对偏离度求一阶导数的变化
        deviation_diff = deviation.diff()
        acceleration = deviation_diff.diff()
        # 对偏离度进行归一化
        deviation_norm = deviation / 0.1  # 假设10%为极端
        deviation_norm = deviation_norm.clip(-1,1)
        # 加速度符号: 正加速度表示趋势加强，负加速度表示趋势减弱
        acc_sign = acceleration.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        # 当偏离度大且加速度与偏离方向相反时，反转风险高
        reversal_risk = -1 * (deviation_norm * acc_sign).clip(-1,1)
        # 平滑
        result = reversal_risk.rolling(3).mean().fillna(0)
        return result
