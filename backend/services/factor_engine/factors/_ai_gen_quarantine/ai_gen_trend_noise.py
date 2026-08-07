"""AI因子: 趋势噪声比因子 | 置信:70% | 基于价格路径效率比（净变化/总波动）衡量趋势清晰度。比值低表示噪声大、趋势不明朗（regime=unknown），因子输出负值；比值高表示强趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendNoiseRatio(BaseFactor):
    """基于价格路径效率比（净变化/总波动）衡量趋势清晰度。比值低表示噪声大、趋势不明朗（regime=unknown），因子输出负值；比值高表示强趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_noise",
            name="Trend Noise Ratio",
            display_name="趋势噪声比因子",
            description="基于价格路径效率比（净变化/总波动）衡量趋势清晰度。比值低表示噪声大、趋势不明朗（regime=unknown），因子输出负值；比值高表示强趋势。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # Price movement efficiency: |close - close.shift(n)| / sum of absolute returns
        n = 14
        net_change = np.abs(data['close'] - data['close'].shift(n))
        total_path = (data['high'] - data['low']).rolling(n).sum()  # approximation of total price movement
        # Avoid division by zero
        efficiency = net_change / (total_path + 1e-10)
        # Normalize to [-1,1] by comparing to a threshold (e.g., 0.5 as middle)
        factor = 2 * (efficiency - 0.5)  # [0,1] -> [-1,1]
        factor = factor.clip(-1, 1)
        return factor.fillna(0)
