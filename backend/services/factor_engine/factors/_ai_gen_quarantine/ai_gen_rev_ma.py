"""AI因子: 均值回复成交量收缩 | 置信:65% | 当价格短期快速偏离长期均线（如收盘价高于60日均线超过5%），且成交量相对20日均量萎缩超过30%时，表明上涨动能衰竭，容易发生回调导致做多亏损。因子输出为负值（-1）表示看空，正值（+1）表示看多，中间值线性映射。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_with_Volume_Contraction(BaseFactor):
    """当价格短期快速偏离长期均线（如收盘价高于60日均线超过5%），且成交量相对20日均量萎缩超过30%时，表明上涨动能衰竭，容易发生回调导致做多亏损。因子输出为负值（-1）表示看空，正值（+1）表示看多，中间值线性映射。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_ma",
            name="Mean Reversion with Volume Contraction",
            display_name="均值回复成交量收缩",
            description="当价格短期快速偏离长期均线（如收盘价高于60日均线超过5%），且成交量相对20日均量萎缩超过30%时，表明上涨动能衰竭，容易发生回调导致做多亏损。因子输出为负值（-1）表示看空，正值（+1）表示看多，中间值线性映射。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']
        ma_long = close.rolling(60, min_periods=60).mean()
        # 价格偏离度 (百分比)
        deviation = (close - ma_long) / ma_long
        vol_ma20 = volume.rolling(20, min_periods=20).mean()
        vol_ratio = volume / vol_ma20
        # 信号：高偏离且低成交量时看空
        score = -deviation * (1 - (vol_ratio.clip(0, 1)))
        # 归一化到[-1,1]
        norm = score / (score.abs().max() + 1e-12)
        return norm.clip(-1, 1)
