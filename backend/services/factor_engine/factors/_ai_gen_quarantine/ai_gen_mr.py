"""AI因子: 均值回复强度 | 置信:60% | 基于价格偏离20日均线的程度和成交量萎缩确认，捕捉反转信号。价格远离均线且成交量收缩时，反转概率高，输出负值对应超买反转，正值对应超卖反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionStrength(BaseFactor):
    """基于价格偏离20日均线的程度和成交量萎缩确认，捕捉反转信号。价格远离均线且成交量收缩时，反转概率高，输出负值对应超买反转，正值对应超卖反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mr",
            name="mean_reversion_strength",
            display_name="均值回复强度",
            description="基于价格偏离20日均线的程度和成交量萎缩确认，捕捉反转信号。价格远离均线且成交量收缩时，反转概率高，输出负值对应超买反转，正值对应超卖反转。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        ma20 = close.rolling(20).mean()
        # 价格偏离度 (z-score)
        dev = (close - ma20) / close.rolling(20).std()
        # 成交量变化率（短期 vs 长期均值）
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma20
        # 缩量信号：vol_ratio < 1 且下降
        vol_shrink = (vol_ratio < 1).astype(float) * (1 - vol_ratio / 1)
        # 偏离方向反转：负偏离（超卖）期望正收益，所以乘以-1
        raw = -dev * vol_shrink
        # 平滑并归一化
        result = raw.rolling(10).mean()
        max_abs = result.abs().rolling(50).max()
        result = result / (max_abs + 1e-10)
        result = result.clip(-1, 1)
        return result.fillna(0.0)
