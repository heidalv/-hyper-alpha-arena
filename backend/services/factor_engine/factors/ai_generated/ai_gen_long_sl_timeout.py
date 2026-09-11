"""AI因子: 多头止损超时综合因子 | 置信:40% | 结合多头在止损和超时两种亏损模式，识别价格弱势且波动率异常导致的假突破。值越高表示价格处于下跌趋势中的反弹乏力，做多风险大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LongStoplossTimeoutCombined(BaseFactor):
    """结合多头在止损和超时两种亏损模式，识别价格弱势且波动率异常导致的假突破。值越高表示价格处于下跌趋势中的反弹乏力，做多风险大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_long_sl_timeout",
            name="Long_StopLoss_Timeout_Combined",
            display_name="多头止损超时综合因子",
            description="结合多头在止损和超时两种亏损模式，识别价格弱势且波动率异常导致的假突破。值越高表示价格处于下跌趋势中的反弹乏力，做多风险大。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        ret5 = close.pct_change(5)
        atr14 = (high - low).rolling(14).mean()
        close_ma = close / close.rolling(20).mean() - 1
        vol_ratio = atr14 / (close.rolling(14).std() + 1e-9)
        result = (close_ma * 0.6 + vol_ratio * 0.4) * (ret5 < 0).astype(float)
        return result.clip(-1, 1)
