"""AI因子: 波动率调整动量 | 置信:60% | 基于近期波动率（ATR）对价格动量进行归一化，避免在波动率异常放大或缩小时误判趋势。当价格偏离均线超过1.5倍ATR时，信号趋于极端；否则在0附近。有助于识别regime unknown下的噪声环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_Momentum(BaseFactor):
    """基于近期波动率（ATR）对价格动量进行归一化，避免在波动率异常放大或缩小时误判趋势。当价格偏离均线超过1.5倍ATR时，信号趋于极端；否则在0附近。有助于识别regime unknown下的噪声环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adjust",
            name="Volatility Adjusted Momentum",
            display_name="波动率调整动量",
            description="基于近期波动率（ATR）对价格动量进行归一化，避免在波动率异常放大或缩小时误判趋势。当价格偏离均线超过1.5倍ATR时，信号趋于极端；否则在0附近。有助于识别regime unknown下的噪声环境。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR (14周期)
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        # 计算20日简单移动平均
        sma20 = close.rolling(20).mean()
        # 价格偏离率： (close - sma20) / atr，并截断在[-3,3]再映射到[-1,1]
        deviation = (close - sma20) / atr
        deviation = deviation.clip(-3, 3)
        result = deviation / 3.0
        return result.fillna(0.0)
