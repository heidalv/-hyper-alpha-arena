"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量与长期动量之差反映动量加速度：短期跑赢长期代表趋势加速（看涨），短期跑输长期代表趋势衰竭（看跌）。用波动率标准化后截断，捕捉趋势延续与反转的临界点。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodMomentumAcceleration(BaseFactor):
    """短期动量与长期动量之差反映动量加速度：短期跑赢长期代表趋势加速（看涨），短期跑输长期代表趋势衰竭（看跌）。用波动率标准化后截断，捕捉趋势延续与反转的临界点。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_10_30",
            name="Multi-period Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量与长期动量之差反映动量加速度：短期跑赢长期代表趋势加速（看涨），短期跑输长期代表趋势衰竭（看跌）。用波动率标准化后截断，捕捉趋势延续与反转的临界点。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_s = data['close'].pct_change(5)
        mom_l = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((mom_s - mom_l) / vol).rolling(3).mean().clip(-1, 1)
        return result
