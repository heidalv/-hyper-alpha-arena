"""AI因子: 成交量冲击反转因子 | 置信:55% | 检测成交量突然放大后价格方向的变化。计算当前成交量相对于过去N周期均值的倍数，乘以价格变动方向（收盘价-开盘价），再取符号并归一化。当成交量放大且价格反向运动时值接近-1或1，预示反转。应对dust_cleanup和hold_timeout_review中的流动性冲击。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeShockReversal(BaseFactor):
    """检测成交量突然放大后价格方向的变化。计算当前成交量相对于过去N周期均值的倍数，乘以价格变动方向（收盘价-开盘价），再取符号并归一化。当成交量放大且价格反向运动时值接近-1或1，预示反转。应对dust_cleanup和hold_timeout_review中的流动性冲击。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volshock",
            name="Volume Shock Reversal",
            display_name="成交量冲击反转因子",
            description="检测成交量突然放大后价格方向的变化。计算当前成交量相对于过去N周期均值的倍数，乘以价格变动方向（收盘价-开盘价），再取符号并归一化。当成交量放大且价格反向运动时值接近-1或1，预示反转。应对dust_cleanup和hold_timeout_review中的流动性冲击。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns open, high, low, close, volume
        window = 10
        vol_ma = data['volume'].rolling(window).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-10)
        price_dir = np.sign(data['close'] - data['open'])
        raw = vol_ratio * price_dir
        # 归一化到[-1,1]：使用tanh压制极端值
        result = np.tanh((raw - 1) * 2)  # 当vol_ratio=1时值为0，放大则趋向±1
        # 修正：直接使用vol_ratio乘方向，然后clip
        # 更好：标准化后clip
        z = (vol_ratio - 1) * price_dir
        result = np.clip(z, -1, 1)
        return result
