"""AI因子: 反转磁铁 | 置信:65% | 检测价格接近近期极值且成交量异常放大时可能发生的反转。计算收盘价在近期（20日）高低点中的位置，再结合成交量相对于均值的偏离，生成[-1,1]信号。正值表示可能向上反转，负值表示可能向下反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversalmagnet(BaseFactor):
    """检测价格接近近期极值且成交量异常放大时可能发生的反转。计算收盘价在近期（20日）高低点中的位置，再结合成交量相对于均值的偏离，生成[-1,1]信号。正值表示可能向上反转，负值表示可能向下反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_magnet",
            name="ReversalMagnet",
            display_name="反转磁铁",
            description="检测价格接近近期极值且成交量异常放大时可能发生的反转。计算收盘价在近期（20日）高低点中的位置，再结合成交量相对于均值的偏离，生成[-1,1]信号。正值表示可能向上反转，负值表示可能向下反转。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 20
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 近期最高最低
        recent_high = high.rolling(n, min_periods=1).max()
        recent_low = low.rolling(n, min_periods=1).min()
        # 价格在区间中的相对位置，[-1,1]：接近高点为-1，接近低点为+1
        pos = 1 - 2 * (close - recent_low) / (recent_high - recent_low + 1e-8)
        # 成交量异常：当前成交量相对于近期均值，标准化
        vol_mean = volume.rolling(n, min_periods=1).mean()
        vol_std = volume.rolling(n, min_periods=1).std()
        vol_z = (volume - vol_mean) / (vol_std + 1e-8)
        # 当价格接近极值且成交量放大时，反转信号更强
        # 将位置符号取反，因为如果接近高点且成交量放大，预期下跌（负信号），故取反
        signal = -pos * np.clip(vol_z, 0, 2)  # 只考虑成交量放大（正偏离）
        # 归一化到[-1,1]
        result = np.clip(signal / 2, -1, 1)
        return result
