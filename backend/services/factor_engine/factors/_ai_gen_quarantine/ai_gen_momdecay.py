"""AI因子: 动量衰减波动飙升因子 | 置信:60% | 计算短期价格动量（过去5分钟收益率）与过去20分钟收益率的标准差之比，当比值快速下降且波动率上升时，表明趋势衰竭，易触发反向止损。使用tanh映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum Decay with Volatility Spike(BaseFactor):
    """计算短期价格动量（过去5分钟收益率）与过去20分钟收益率的标准差之比，当比值快速下降且波动率上升时，表明趋势衰竭，易触发反向止损。使用tanh映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momdecay",
            name="Momentum Decay with Volatility Spike",
            display_name="动量衰减波动飙升因子",
            description="计算短期价格动量（过去5分钟收益率）与过去20分钟收益率的标准差之比，当比值快速下降且波动率上升时，表明趋势衰竭，易触发反向止损。使用tanh映射到[-1,1]。",
            category="behavioral",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            ret5 = data['close'].pct_change(5)
            vol20 = data['close'].pct_change().rolling(20).std()
            ratio = ret5 / (vol20 + 1e-8)
            ratio = ratio.rolling(5).mean()  # 平滑
            # 动量衰减信号：ratio下降且vol上升
            decay = -ratio.diff() * vol20
            result = decay.fillna(0).clip(-1,1)
            return result
