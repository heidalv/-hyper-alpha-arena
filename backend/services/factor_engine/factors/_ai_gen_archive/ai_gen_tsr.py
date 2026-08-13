"""AI因子: 趋势强度反转因子 | 置信:60% | 结合短期均线斜率与波动率变化，当短期均线转负且波动率上升时，表明趋势可能反转下行，多头风险加大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Strength_Reversal(BaseFactor):
    """结合短期均线斜率与波动率变化，当短期均线转负且波动率上升时，表明趋势可能反转下行，多头风险加大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tsr",
            name="Trend Strength Reversal",
            display_name="趋势强度反转因子",
            description="结合短期均线斜率与波动率变化，当短期均线转负且波动率上升时，表明趋势可能反转下行，多头风险加大。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        short_ma = close.rolling(5).mean()
        long_ma = close.rolling(20).mean()
        sr_slope = short_ma.diff(3) / short_ma.shift(3)  # 3周期斜率
        volatility = close.pct_change().rolling(10).std()
        vol_ratio = volatility / volatility.rolling(20).mean()  # 波动率相对水平
        # 当短期均线向下且波动率放大时负向信号
        bearish = (sr_slope < -0.005) & (vol_ratio > 1.2)
        signal = -bearish.astype(float) * 1.0
        # 使用长周期均线关系增强：短期低于长期
        bearish2 = (short_ma < long_ma) & (sr_slope < 0)
        signal = signal + (-bearish2.astype(float) * 0.5)
        signal = signal.clip(-1, 1)
        return signal.fillna(0)
