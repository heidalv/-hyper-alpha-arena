"""AI因子: 流动性磁石反转 | 置信:70% | 检测价格接近近期最高/最低点且伴随成交量异常放大，预示可能反转。计算收盘价相对于过去N日最高最低的位置，结合成交量相对于均量的偏离，在极端位置给出负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversal(BaseFactor):
    """检测价格接近近期最高/最低点且伴随成交量异常放大，预示可能反转。计算收盘价相对于过去N日最高最低的位置，结合成交量相对于均量的偏离，在极端位置给出负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lqm",
            name="Liquidity Magnet Reversal",
            display_name="流动性磁石反转",
            description="检测价格接近近期最高/最低点且伴随成交量异常放大，预示可能反转。计算收盘价相对于过去N日最高最低的位置，结合成交量相对于均量的偏离，在极端位置给出负向信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # data has columns: open, high, low, close, volume
        n = 20
        recent_high = data['high'].rolling(n).max()
        recent_low = data['low'].rolling(n).min()
        price_position = (data['close'] - recent_low) / (recent_high - recent_low + 1e-10)
        # 极端位置：接近0或1
        extreme = ((price_position < 0.1) | (price_position > 0.9)).astype(float)
        # 成交量异常：当前量相对于过去n日均量的倍数
        avg_vol = data['volume'].rolling(n).mean()
        vol_ratio = data['volume'] / (avg_vol + 1e-10)
        vol_surge = (vol_ratio > 1.5).astype(float)
        # 组合：极端位置且成交量放大时预示反转，信号方向与位置相反（靠近顶部做空，底部做多）
        signal = - (price_position - 0.5) * 2 * extreme * vol_surge
        # 平滑并映射到[-1,1]
        result = signal.rolling(3).mean().fillna(0).clip(-1, 1)
        return result
