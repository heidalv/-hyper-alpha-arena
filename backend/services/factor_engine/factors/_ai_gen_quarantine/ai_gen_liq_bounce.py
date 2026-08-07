"""AI因子: 流动性磁铁反弹 | 置信:65% | 检测价格迅速接近近期高点/低点（流动性磁铁区）后出现反转的迹象，使用价格极值附近波动率和成交量的突变。专门针对liq_magnet_reversal亏损模式设计。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetBounce(BaseFactor):
    """检测价格迅速接近近期高点/低点（流动性磁铁区）后出现反转的迹象，使用价格极值附近波动率和成交量的突变。专门针对liq_magnet_reversal亏损模式设计。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_bounce",
            name="Liquidity Magnet Bounce",
            display_name="流动性磁铁反弹",
            description="检测价格迅速接近近期高点/低点（流动性磁铁区）后出现反转的迹象，使用价格极值附近波动率和成交量的突变。专门针对liq_magnet_reversal亏损模式设计。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 近期最高最低点（10周期）
        recent_high = high.rolling(10).max()
        recent_low = low.rolling(10).min()
        # 距离高点/低点的百分比
        dist_high = (recent_high - close) / recent_high
        dist_low = (close - recent_low) / recent_low
        # 成交量爆发
        vol_surge = volume > volume.rolling(20).mean() * 1.8
        # 若价格接近高点（距离<1%）且成交量异常，看空反转；若接近低点且成交量异常，看多反转
        signal = np.where((dist_high < 0.01) & vol_surge, -1,
                          np.where((dist_low < 0.01) & vol_surge, 1, 0))
        return pd.Series(signal, index=close.index)
