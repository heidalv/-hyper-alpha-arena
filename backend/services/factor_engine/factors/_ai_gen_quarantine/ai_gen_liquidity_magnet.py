"""AI因子: 流动性磁铁反转 | 置信:60% | 检测价格是否快速接近近期极值（高点或低点）且伴随成交量放大，这通常意味着流动性磁铁效应，可能触发反转。通过计算价格与最近N根K线最高/最低的距离，并结合成交量异常，输出正值表示看涨反转概率高，负值表示看跌反转概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversal(BaseFactor):
    """检测价格是否快速接近近期极值（高点或低点）且伴随成交量放大，这通常意味着流动性磁铁效应，可能触发反转。通过计算价格与最近N根K线最高/最低的距离，并结合成交量异常，输出正值表示看涨反转概率高，负值表示看跌反转概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity_magnet",
            name="Liquidity Magnet Reversal",
            display_name="流动性磁铁反转",
            description="检测价格是否快速接近近期极值（高点或低点）且伴随成交量放大，这通常意味着流动性磁铁效应，可能触发反转。通过计算价格与最近N根K线最高/最低的距离，并结合成交量异常，输出正值表示看涨反转概率高，负值表示看跌反转概率高。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        n = 10
        # 近期极值
        recent_high = data['high'].rolling(window=n).max()
        recent_low = data['low'].rolling(window=n).min()
        # 价格到极值的距离（百分比）
        dist_to_high = (recent_high - data['close']) / (recent_high - recent_low + 1e-10)
        dist_to_low = (data['close'] - recent_low) / (recent_high - recent_low + 1e-10)
        # 成交量异常因子（当前成交量/平均成交量）
        avg_vol = data['volume'].rolling(window=n).mean()
        vol_surge = data['volume'] / (avg_vol + 1e-10)
        # 综合反转信号：靠近高点且放量 -> 看跌（负值）；靠近低点且放量 -> 看涨（正值）
        reversal_signal = dist_to_low * vol_surge - dist_to_high * vol_surge
        # 归一化
        return np.clip(reversal_signal * 2, -1, 1)
