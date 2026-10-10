"""AI因子: 动量加速度 | 置信:60% | 用短周期收益减去长周期收益衡量动量加速度：短期强于长期表示动量加速上行，未来延续上涨概率高；短期弱于长期表示动量衰减，未来下跌概率高。捕捉趋势加速阶段的alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """用短周期收益减去长周期收益衡量动量加速度：短期强于长期表示动量加速上行，未来延续上涨概率高；短期弱于长期表示动量衰减，未来下跌概率高。捕捉趋势加速阶段的alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="用短周期收益减去长周期收益衡量动量加速度：短期强于长期表示动量加速上行，未来延续上涨概率高；短期弱于长期表示动量衰减，未来下跌概率高。捕捉趋势加速阶段的alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ret = data['close'].pct_change(5)
        long_ret = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short_ret - long_ret) / vol).clip(-1, 1)
        return result
