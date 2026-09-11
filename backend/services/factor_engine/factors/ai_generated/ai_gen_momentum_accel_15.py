"""AI因子: 动量加速度因子 | 置信:60% | 多周期动量差（5日收益减20日收益）衡量短期相对长期的动量加速度。正值表示短期动能强于长期趋势，趋势延续概率高；负值表示动能衰减。结合波动率归一化以降低高波动噪声，输出[-1,1]打分，捕捉趋势加速的 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """多周期动量差（5日收益减20日收益）衡量短期相对长期的动量加速度。正值表示短期动能强于长期趋势，趋势延续概率高；负值表示动能衰减。结合波动率归一化以降低高波动噪声，输出[-1,1]打分，捕捉趋势加速的 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_15",
            name="Momentum Acceleration",
            display_name="动量加速度因子",
            description="多周期动量差（5日收益减20日收益）衡量短期相对长期的动量加速度。正值表示短期动能强于长期趋势，趋势延续概率高；负值表示动能衰减。结合波动率归一化以降低高波动噪声，输出[-1,1]打分，捕捉趋势加速的 alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom = data['close'].pct_change(5) - data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = (mom / (vol * 10 + 1e-9)).rolling(3).mean().clip(-1, 1)
        return result
