"""AI因子: 相对成交量反转 | 置信:55% | 判断价格是否处于近期极端位置且成交量异常放大，如果是则返回反转信号（均值回复）。计算当前价格相对于N日最高最低的位置，结合成交量相对均值的倍数，生成[-1,1]信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Relative_Volume_Reversal(BaseFactor):
    """判断价格是否处于近期极端位置且成交量异常放大，如果是则返回反转信号（均值回复）。计算当前价格相对于N日最高最低的位置，结合成交量相对均值的倍数，生成[-1,1]信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rvol",
            name="Relative Volume Reversal",
            display_name="相对成交量反转",
            description="判断价格是否处于近期极端位置且成交量异常放大，如果是则返回反转信号（均值回复）。计算当前价格相对于N日最高最低的位置，结合成交量相对均值的倍数，生成[-1,1]信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 14
        close = data['close']
        high = data['high'].rolling(n).max()
        low = data['low'].rolling(n).min()
        pos = (close - low) / (high - low + 1e-10)  # 0~1
        avg_vol = data['volume'].rolling(n).mean()
        vol_ratio = data['volume'] / (avg_vol + 1e-10)
        # 极值位置且量异常大 -> 反转
        signal = np.where((pos > 0.9) & (vol_ratio > 1.5), -1, np.where((pos < 0.1) & (vol_ratio > 1.5), 1, 0))
        # 用pos中心化后平滑
        result = pd.Series(signal, index=close.index).astype(float)
        return result
