"""AI因子: 波动率调整动量加速度 | 置信:58% | 用5日与20日收益率之差衡量动量加速度，再除以20日已实现波动率做风险调整，剔除高波动噪声对动量的干扰。加速度为正说明短期动能强于中期趋势，未来上涨概率更高；为负则相反。clip至[-1,1]输出。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """用5日与20日收益率之差衡量动量加速度，再除以20日已实现波动率做风险调整，剔除高波动噪声对动量的干扰。加速度为正说明短期动能强于中期趋势，未来上涨概率更高；为负则相反。clip至[-1,1]输出。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adj_momentum_accel",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动率调整动量加速度",
            description="用5日与20日收益率之差衡量动量加速度，再除以20日已实现波动率做风险调整，剔除高波动噪声对动量的干扰。加速度为正说明短期动能强于中期趋势，未来上涨概率更高；为负则相反。clip至[-1,1]输出。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((fast - slow) / vol).clip(-1, 1)
        return result
