"""AI因子: 极端反转风险因子 | 置信:70% | 结合14日RSI和成交量在极端水平的表现，当RSI>80且成交量高于其20日均值两倍时，给出负信号（预示潜在反转下跌）；当RSI<20且成交量异常放大时，给出正信号（预示潜在反弹）。用于捕捉因过度追涨杀跌导致的止损亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ExtremeReversalRisk(BaseFactor):
    """结合14日RSI和成交量在极端水平的表现，当RSI>80且成交量高于其20日均值两倍时，给出负信号（预示潜在反转下跌）；当RSI<20且成交量异常放大时，给出正信号（预示潜在反弹）。用于捕捉因过度追涨杀跌导致的止损亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_extreme_reversal",
            name="Extreme Reversal Risk",
            display_name="极端反转风险因子",
            description="结合14日RSI和成交量在极端水平的表现，当RSI>80且成交量高于其20日均值两倍时，给出负信号（预示潜在反转下跌）；当RSI<20且成交量异常放大时，给出正信号（预示潜在反弹）。用于捕捉因过度追涨杀跌导致的止损亏损。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # RSI(14)
        delta = df['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(span=14, adjust=False).mean()
        avg_loss = loss.ewm(span=14, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        df['rsi'] = 100 - 100 / (1 + rs)
        # 成交量均值
        df['vol_ma20'] = df['volume'].rolling(20).mean()
        df['vol_ratio'] = df['volume'] / (df['vol_ma20'] + 1e-10)
        # 极端条件
        overbought = (df['rsi'] > 80) & (df['vol_ratio'] > 2.0)
        oversold = (df['rsi'] < 20) & (df['vol_ratio'] > 2.0)
        signal = pd.Series(0.0, index=df.index)
        signal[overbought] = -1.0
        signal[oversold] = 1.0
        return signal
