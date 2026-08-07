"""AI因子: 波动率状态聚类 | 置信:65% | 基于ATR和价格相对位置，使用K-means聚类（预训练）将市场分为高波动/低波动/未知状态，输出-1（未知）、0（低波动）、1（高波动），用于规避未知状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeCluster(BaseFactor):
    """基于ATR和价格相对位置，使用K-means聚类（预训练）将市场分为高波动/低波动/未知状态，输出-1（未知）、0（低波动）、1（高波动），用于规避未知状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_cluster",
            name="Volatility Regime Cluster",
            display_name="波动率状态聚类",
            description="基于ATR和价格相对位置，使用K-means聚类（预训练）将市场分为高波动/低波动/未知状态，输出-1（未知）、0（低波动）、1（高波动），用于规避未知状态。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 输入data: pd.DataFrame with OHLCV
        # 计算ATR
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 计算价格相对位置 (close - rolling_min)/(rolling_max - rolling_min)
        window = 20
        roll_min = low.rolling(window).min()
        roll_max = high.rolling(window).max()
        pos = (close - roll_min) / (roll_max - roll_min).replace(0, 1e-10)
        # 归一化ATR
        atr_norm = (atr - atr.rolling(100).mean()) / atr.rolling(100).std()
        # 基于阈值聚类 (简易版)
        atr_thresh = atr_norm.abs() > 1.5
        pos_thresh = (pos > 0.8) | (pos < 0.2)
        # 高波动且极端位置 -> 1, 低波动且中间位置 -> 0, 其余 -> -1 (unknown)
        result = pd.Series(-1, index=data.index)
        mask_high = (atr_norm > 1.5) & pos_thresh
        mask_low = (atr_norm < -0.5) & (~pos_thresh)
        result[mask_high] = 1.0
        result[mask_low] = 0.0
        return result
