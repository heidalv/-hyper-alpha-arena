"""AI因子: 量价不平衡因子 | 置信:60% | 计算过去10根K线内，阳线成交量与阴线成交量的比值，再乘以价格方向（上涨为+1，下跌为-1），识别主力资金流向的异常。当比值极端且价格反向运动时，易发生反转亏损。使用tanh归一化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume-Weighted Price Imbalance(BaseFactor):
    """计算过去10根K线内，阳线成交量与阴线成交量的比值，再乘以价格方向（上涨为+1，下跌为-1），识别主力资金流向的异常。当比值极端且价格反向运动时，易发生反转亏损。使用tanh归一化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volimbalance",
            name="Volume-Weighted Price Imbalance",
            display_name="量价不平衡因子",
            description="计算过去10根K线内，阳线成交量与阴线成交量的比值，再乘以价格方向（上涨为+1，下跌为-1），识别主力资金流向的异常。当比值极端且价格反向运动时，易发生反转亏损。使用tanh归一化。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            up = (data['close'] > data['open']).astype(float)
            down = (data['close'] < data['open']).astype(float)
            vol_up = data['volume'] * up
            vol_down = data['volume'] * down
            ratio = vol_up.rolling(10).sum() / (vol_down.rolling(10).sum() + 1e-8)
            direction = (data['close'] - data['open']).rolling(10).mean()
            signal = ratio * direction.sign()
            result = (signal - signal.rolling(50).mean()) / (signal.rolling(50).std() + 1e-8)
            result = result.fillna(0).clip(-1,1)
            return result
