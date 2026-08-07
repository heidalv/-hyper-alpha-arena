"""AI因子: 噪音比率因子 | 置信:60% | 计算价格序列的噪音比率，即价格变动中随机波动的比例。通过比较实际波动与理论趋势波动，高噪音比率表明市场无序，容易触发止损。因子值接近+1时噪音大，接近-1时趋势清晰。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NoiseRatioFactor(BaseFactor):
    """计算价格序列的噪音比率，即价格变动中随机波动的比例。通过比较实际波动与理论趋势波动，高噪音比率表明市场无序，容易触发止损。因子值接近+1时噪音大，接近-1时趋势清晰。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_noise",
            name="Noise Ratio Factor",
            display_name="噪音比率因子",
            description="计算价格序列的噪音比率，即价格变动中随机波动的比例。通过比较实际波动与理论趋势波动，高噪音比率表明市场无序，容易触发止损。因子值接近+1时噪音大，接近-1时趋势清晰。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 真实波幅与方向波幅
        ret = close.pct_change()
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        # 方向性移动：用收盘价变化绝对值
        directional = np.abs(ret) * close.shift(1)
        # 噪音 = TR - 方向性移动
        noise = tr - directional
        # 噪音比率
        noise_ratio = noise / (tr + 1e-10)
        n = 20
        noise_ma = noise_ratio.rolling(n).mean()
        result = 2 * noise_ma - 1
        return result.fillna(0).clip(-1, 1)
