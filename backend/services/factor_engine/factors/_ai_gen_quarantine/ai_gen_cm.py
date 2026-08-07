"""AI因子: 震荡市检测器 | 置信:60% | 基于价格效率比（ER）识别市场方向性。ER接近0表示震荡无趋势，此环境下持仓易触发hold_timeout或利润回撤；返回接近-1，建议回避开仓。正值表示强趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ChoppyMarketDetector(BaseFactor):
    """基于价格效率比（ER）识别市场方向性。ER接近0表示震荡无趋势，此环境下持仓易触发hold_timeout或利润回撤；返回接近-1，建议回避开仓。正值表示强趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_cm",
            name="Choppy Market Detector",
            display_name="震荡市检测器",
            description="基于价格效率比（ER）识别市场方向性。ER接近0表示震荡无趋势，此环境下持仓易触发hold_timeout或利润回撤；返回接近-1，建议回避开仓。正值表示强趋势。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        period = 14
        direction = close.diff(period).abs()
        volatility = close.diff().abs().rolling(period).sum()
        er = direction / (volatility + 1e-8)
        result = 2 * er - 1
        result = result.replace([float('inf'), -float('inf')], 0).fillna(0).clip(-1, 1)
        return result
