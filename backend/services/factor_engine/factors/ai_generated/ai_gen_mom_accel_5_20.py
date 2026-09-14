"""AI因子: 动量加速度 | 置信:60% | 短期5日动量减去20日动量，捕捉动量加速度：短周期跑赢长周期时为正，代表趋势加速上行；反之减速。做多加速、做空减速。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration520(BaseFactor):
    """短期5日动量减去20日动量，捕捉动量加速度：短周期跑赢长周期时为正，代表趋势加速上行；反之减速。做多加速、做空减速。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_5_20",
            name="Momentum Acceleration 5-20",
            display_name="动量加速度",
            description="短期5日动量减去20日动量，捕捉动量加速度：短周期跑赢长周期时为正，代表趋势加速上行；反之减速。做多加速、做空减速。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom5 = data['close'].pct_change(5)
        mom20 = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((mom5 - mom20) / vol).rolling(3).mean().clip(-1, 1)
        return result
