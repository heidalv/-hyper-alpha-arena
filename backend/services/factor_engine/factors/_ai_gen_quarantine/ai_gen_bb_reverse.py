"""AI因子: 布林带挤压反转因子 | 置信:60% | 当布林带宽度处于历史低位（挤压），且价格偏离中轨较大时，发出均值回归信号。用于捕捉假突破后的反转，避免追高或追低带来的超时持有亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerBandSqueezeReversal(BaseFactor):
    """当布林带宽度处于历史低位（挤压），且价格偏离中轨较大时，发出均值回归信号。用于捕捉假突破后的反转，避免追高或追低带来的超时持有亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bb_reverse",
            name="Bollinger Band Squeeze Reversal",
            display_name="布林带挤压反转因子",
            description="当布林带宽度处于历史低位（挤压），且价格偏离中轨较大时，发出均值回归信号。用于捕捉假突破后的反转，避免追高或追低带来的超时持有亏损。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        period = 20
        ma = close.rolling(period).mean()
        std = close.rolling(period).std()
        bb_width = 4 * std / ma
        max_width = bb_width.rolling(200).max()
        squeeze = 1 - (bb_width / max_width)
        price_dev = (close - ma) / (2 * std)
        raw = -price_dev * squeeze
        return raw.clip(-1, 1)
