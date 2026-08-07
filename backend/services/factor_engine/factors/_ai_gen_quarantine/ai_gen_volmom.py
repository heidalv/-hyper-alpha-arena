"""AI因子: 波动调整动量 | 置信:70% | 计算过去N日收益率除以过去N日波动率（标准差），以识别在低波动环境下趋势持续性较高，高波动环境下趋势易反转。返回-1到1，负值表示高波动下动量衰竭，不宜追多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_Momentum(BaseFactor):
    """计算过去N日收益率除以过去N日波动率（标准差），以识别在低波动环境下趋势持续性较高，高波动环境下趋势易反转。返回-1到1，负值表示高波动下动量衰竭，不宜追多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volmom",
            name="Volatility-Adjusted Momentum",
            display_name="波动调整动量",
            description="计算过去N日收益率除以过去N日波动率（标准差），以识别在低波动环境下趋势持续性较高，高波动环境下趋势易反转。返回-1到1，负值表示高波动下动量衰竭，不宜追多。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        vol = close.pct_change().rolling(14).std()
        ret = close.pct_change(14)
        raw = ret / (vol + 1e-10)
        norm = (raw - raw.rolling(50).mean()) / raw.rolling(50).std()
        result = norm.clip(-3, 3) / 3
        return result.fillna(0)
