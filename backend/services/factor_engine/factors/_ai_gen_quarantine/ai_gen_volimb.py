"""AI因子: 成交量不平衡因子 | 置信:60% | 基于买卖成交量差异累积的动量，反映资金主动推动方向。当成交量失衡方向与价格趋势不一致时，容易导致持仓超时。因子值+1表示强烈买入失衡，-1表示强烈卖出失衡。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeImbalance(BaseFactor):
    """基于买卖成交量差异累积的动量，反映资金主动推动方向。当成交量失衡方向与价格趋势不一致时，容易导致持仓超时。因子值+1表示强烈买入失衡，-1表示强烈卖出失衡。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volimb",
            name="Volume Imbalance",
            display_name="成交量不平衡因子",
            description="基于买卖成交量差异累积的动量，反映资金主动推动方向。当成交量失衡方向与价格趋势不一致时，容易导致持仓超时。因子值+1表示强烈买入失衡，-1表示强烈卖出失衡。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        open_ = data['open']
        volume = data['volume']
        direction = (close > open_).astype(int) - (close < open_).astype(int)
        signed_volume = direction * volume
        cum_vol = signed_volume.rolling(10).sum()
        norm_factor = volume.rolling(10).sum() + 1e-9
        result = (cum_vol / norm_factor).clip(-1, 1)
        return result
