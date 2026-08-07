"""AI因子: 动量衰减反转 | 置信:55% | 识别短期趋势衰竭：计算近期价格动量（如ROC）与成交量变化的方向背离。当动量指标下降但价格仍创新高/新低，且成交量萎缩，预示趋势反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumDecayReversal(BaseFactor):
    """识别短期趋势衰竭：计算近期价格动量（如ROC）与成交量变化的方向背离。当动量指标下降但价格仍创新高/新低，且成交量萎缩，预示趋势反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mtmdecay",
            name="Momentum Decay Reversal",
            display_name="动量衰减反转",
            description="识别短期趋势衰竭：计算近期价格动量（如ROC）与成交量变化的方向背离。当动量指标下降但价格仍创新高/新低，且成交量萎缩，预示趋势反转。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 动量：过去5日变化率
        roc = data['close'].pct_change(5)
        # 成交量变化率
        vol_change = data['volume'].pct_change(5)
        # 新高/新低判断
        recent_high = data['high'].rolling(10).max()
        recent_low = data['low'].rolling(10).min()
        new_high = data['high'] == recent_high
        new_low = data['low'] == recent_low
        # 背离条件：价格创新高但动量下降且成交萎缩 => 看空
        bearish = new_high & (roc < roc.shift(1)) & (vol_change < 0)
        bullish = new_low & (roc > roc.shift(1)) & (vol_change < 0)
        signal = np.where(bearish, -1, np.where(bullish, 1, 0))
        return pd.Series(signal, index=data.index)
