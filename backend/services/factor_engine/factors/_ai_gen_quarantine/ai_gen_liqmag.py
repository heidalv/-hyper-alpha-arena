"""AI因子: 流动性磁铁反转因子 | 置信:60% | 检测价格快速接近近期高价或低价时，伴随成交量异常放大后的反转风险。当价格接近布林带上轨且成交量飙升时，做空信号；接近下轨且成交量飙升时，做多信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversal(BaseFactor):
    """检测价格快速接近近期高价或低价时，伴随成交量异常放大后的反转风险。当价格接近布林带上轨且成交量飙升时，做空信号；接近下轨且成交量飙升时，做多信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liqmag",
            name="Liquidity Magnet Reversal",
            display_name="流动性磁铁反转因子",
            description="检测价格快速接近近期高价或低价时，伴随成交量异常放大后的反转风险。当价格接近布林带上轨且成交量飙升时，做空信号；接近下轨且成交量飙升时，做多信号。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        period = 20
        # 计算布林带
        sma = data['close'].rolling(period).mean()
        std = data['close'].rolling(period).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        # 计算价格位置 (0~1之间)
        pos = (data['close'] - lower) / (upper - lower + 1e-8)
        # 成交量相对变化
        vol_ma = data['volume'].rolling(5).mean()
        vol_spike = data['volume'] / (vol_ma + 1e-8)
        # 当价格触及极端位置且成交量放大时，产生反向信号
        buy_signal = (pos < 0.1) & (vol_spike > 1.5)
        sell_signal = (pos > 0.9) & (vol_spike > 1.5)
        # 编码为-1到1
        signal = pd.Series(0.0, index=data.index)
        signal[buy_signal] = 1.0
        signal[sell_signal] = -1.0
        # 平滑并限制
        return signal
