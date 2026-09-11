"""AI因子: 动量加速度 | 置信:58% | 用短周期动量减去长周期动量衡量动量加速度：短强长弱（正加速）预示趋势延续上涨，短弱长强（负加速）预示回落。捕捉多周期动量结构变化带来的方向性 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """用短周期动量减去长周期动量衡量动量加速度：短强长弱（正加速）预示趋势延续上涨，短弱长强（负加速）预示回落。捕捉多周期动量结构变化带来的方向性 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="用短周期动量减去长周期动量衡量动量加速度：短强长弱（正加速）预示趋势延续上涨，短弱长强（负加速）预示回落。捕捉多周期动量结构变化带来的方向性 alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short - long) / vol).clip(-1, 1)
        return result
