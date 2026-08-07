"""AI因子: 市场状态噪音 | 置信:55% | 通过波动率与成交量的相对变化衡量市场状态的噪音程度，当波动率异常高而成交量异常低时，表明市场缺乏方向性流动性，容易产生反转亏损。计算ATR与成交量的比值滚动Z-score，并映射到[-1,1]。正值表示噪音高（危险区域），负值表示正常。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regimenoise(BaseFactor):
    """通过波动率与成交量的相对变化衡量市场状态的噪音程度，当波动率异常高而成交量异常低时，表明市场缺乏方向性流动性，容易产生反转亏损。计算ATR与成交量的比值滚动Z-score，并映射到[-1,1]。正值表示噪音高（危险区域），负值表示正常。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_noise",
            name="RegimeNoise",
            display_name="市场状态噪音",
            description="通过波动率与成交量的相对变化衡量市场状态的噪音程度，当波动率异常高而成交量异常低时，表明市场缺乏方向性流动性，容易产生反转亏损。计算ATR与成交量的比值滚动Z-score，并映射到[-1,1]。正值表示噪音高（危险区域），负值表示正常。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算ATR（平均真实波幅）
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=1).mean()
        volume = data['volume']
        # 成交量平滑
        vol_smooth = volume.rolling(14, min_periods=1).mean()
        # 比率：ATR / 成交量（避免除0）
        ratio = atr / (vol_smooth + 1e-8)
        # 滚动Z-score
        window = 30
        mean = ratio.rolling(window, min_periods=1).mean()
        std = ratio.rolling(window, min_periods=1).std()
        z = (ratio - mean) / (std + 1e-8)
        # 截断到[-1,1]
        result = np.clip(z, -1, 1)
        return result
