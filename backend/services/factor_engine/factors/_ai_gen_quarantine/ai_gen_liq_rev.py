"""AI因子: 流动性磁铁反转因子 | 置信:60% | 捕捉价格快速下跌伴随成交量激增后的反弹信号，模拟liq_magnet_reversal亏损模式。计算过去4根K线内价格从近期最高点下跌的幅度，并结合成交量相对过去20日均值的倍数，当跌幅大且量能异常时给出反转预期。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidationMagnetReversalFactor(BaseFactor):
    """捕捉价格快速下跌伴随成交量激增后的反弹信号，模拟liq_magnet_reversal亏损模式。计算过去4根K线内价格从近期最高点下跌的幅度，并结合成交量相对过去20日均值的倍数，当跌幅大且量能异常时给出反转预期。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_rev",
            name="Liquidation Magnet Reversal Factor",
            display_name="流动性磁铁反转因子",
            description="捕捉价格快速下跌伴随成交量激增后的反弹信号，模拟liq_magnet_reversal亏损模式。计算过去4根K线内价格从近期最高点下跌的幅度，并结合成交量相对过去20日均值的倍数，当跌幅大且量能异常时给出反转预期。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 输入data包含open,high,low,close,volume
        df = data.copy()
        # 计算近期高点（过去4根K线最高价）
        recent_high = df['high'].rolling(4).max()
        # 当前价格相对近期高点的跌幅
        drop_pct = (df['close'] - recent_high) / recent_high  # 负值表示下跌
        # 成交量相对过去20日均值的倍数
        vol_ma20 = df['volume'].rolling(20).mean()
        vol_ratio = df['volume'] / vol_ma20
        # 生成原始信号：跌幅越深且成交量倍数越大，预期反弹越强
        raw_signal = -drop_pct * (vol_ratio - 1).clip(lower=0)
        # 用过去100根K线的标准差标准化，并截断到[-1,1]
        std = raw_signal.rolling(100).std()
        z = raw_signal / (std + 1e-9)
        result = z.clip(-1, 1)
        return result
