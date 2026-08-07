"""AI因子: 趋势一致性 | 置信:60% | 通过比较短期(3日)和长期(10日)收益率的方向一致性，并结合波动率缩放，识别稳定趋势。当两者同向且幅度较大时为正，反向时为负，震荡时接近0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConsistency(BaseFactor):
    """通过比较短期(3日)和长期(10日)收益率的方向一致性，并结合波动率缩放，识别稳定趋势。当两者同向且幅度较大时为正，反向时为负，震荡时接近0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tc",
            name="trend_consistency",
            display_name="趋势一致性",
            description="通过比较短期(3日)和长期(10日)收益率的方向一致性，并结合波动率缩放，识别稳定趋势。当两者同向且幅度较大时为正，反向时为负，震荡时接近0。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ret_short = close.pct_change(3)
        ret_long = close.pct_change(10)
        # 方向一致信号
        same_sign = (ret_short * ret_long) > 0
        magnitude = (ret_short.abs() + ret_long.abs()) / 2
        # 添加波动率衰减：高波动时降低置信度
        atr = (data['high'] - data['low']).rolling(14).mean()
        vol_scale = atr / atr.rolling(50).mean()
        vol_scale = vol_scale.clip(0.5, 2.0)
        raw = same_sign.astype(float) * 2 - 1  # 1 or -1
        raw = raw * magnitude * (1 / vol_scale)
        # 归一化到[-1,1]
        norm = raw.rolling(20).std()
        result = raw / (norm + 1e-10)
        result = result.clip(-1, 1)
        return result.fillna(0.0)
