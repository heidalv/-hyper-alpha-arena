"""AI因子: 均值回归风险 | 置信:60% | 计算收盘价相对50日移动平均的偏离度，用ATR标准化后取负，用于识别价格过度偏离均值时的高风险区域，避免在极端位置追涨杀跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionRisk(BaseFactor):
    """计算收盘价相对50日移动平均的偏离度，用ATR标准化后取负，用于识别价格过度偏离均值时的高风险区域，避免在极端位置追涨杀跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrr",
            name="Mean Reversion Risk",
            display_name="均值回归风险",
            description="计算收盘价相对50日移动平均的偏离度，用ATR标准化后取负，用于识别价格过度偏离均值时的高风险区域，避免在极端位置追涨杀跌。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 50日均线
        ma50 = close.rolling(window=50).mean()
        # ATR14
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr14 = tr.rolling(window=14).mean()
        # 标准化偏离
        z = (close - ma50) / atr14
        # 限制极端值并取负，使得价格高于均线时因子为负（风险信号）
        result = -np.tanh(z)
        return result.fillna(0.0)
