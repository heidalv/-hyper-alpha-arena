"""AI因子: 价格跳变不连续性因子 | 置信:50% | 衡量价格在高低价之间的异常波动幅度和频率，捕捉类似dust_cleanup和reverse_netting中的瞬间跳变风险"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceJumpDiscontinuity(BaseFactor):
    """衡量价格在高低价之间的异常波动幅度和频率，捕捉类似dust_cleanup和reverse_netting中的瞬间跳变风险"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_price_jump",
            name="Price Jump Discontinuity",
            display_name="价格跳变不连续性因子",
            description="衡量价格在高低价之间的异常波动幅度和频率，捕捉类似dust_cleanup和reverse_netting中的瞬间跳变风险",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 日内振幅比率
        amp = (high - low) / close.shift(1)
        # 计算ATR (14周期)
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 跳变因子: 当前振幅超过3倍ATR时视为跳变，赋负值
        jump = (amp > 3 * atr / close.shift(1)).astype(float) * -1.0
        # 同时考虑连续跳变衰减
        result = jump.rolling(3).sum().fillna(0).clip(-1,1)
        return result
