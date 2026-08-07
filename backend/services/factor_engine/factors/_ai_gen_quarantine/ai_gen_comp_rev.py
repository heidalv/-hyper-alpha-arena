"""AI因子: 复合反转因子 | 置信:60% | 综合多个技术指标识别超卖后的反转机会，结合RSI、布林带和成交量确认。当RSI低于30且价格跌破布林带下轨时，产生正向信号；当RSI高于70且价格突破上轨时，产生负向信号。信号强度由远离程度和成交量放大系数调整。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class CompositeReversalFactor(BaseFactor):
    """综合多个技术指标识别超卖后的反转机会，结合RSI、布林带和成交量确认。当RSI低于30且价格跌破布林带下轨时，产生正向信号；当RSI高于70且价格突破上轨时，产生负向信号。信号强度由远离程度和成交量放大系数调整。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_comp_rev",
            name="Composite Reversal Factor",
            display_name="复合反转因子",
            description="综合多个技术指标识别超卖后的反转机会，结合RSI、布林带和成交量确认。当RSI低于30且价格跌破布林带下轨时，产生正向信号；当RSI高于70且价格突破上轨时，产生负向信号。信号强度由远离程度和成交量放大系数调整。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # RSI 14
        delta = df['close'].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rsi = 100 - 100 / (1 + gain / (loss + 1e-9))
        # 布林带(20,2)
        ma20 = df['close'].rolling(20).mean()
        std20 = df['close'].rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        # 成交量相对20日均值
        vol_ratio = df['volume'] / df['volume'].rolling(20).mean()
        # 多头反转信号：RSI<30 且 close < lower
        long_signal = ((rsi < 30) & (df['close'] < lower)).astype(float)
        # 空头反转信号：RSI>70 且 close > upper
        short_signal = ((rsi > 70) & (df['close'] > upper)).astype(float)
        # 用远离布林带的程度和成交量放大系数调整强度
        long_strength = ((lower - df['close']) / (std20 + 1e-9)).clip(0, 3) / 3
        short_strength = ((df['close'] - upper) / (std20 + 1e-9)).clip(0, 3) / 3
        # 综合信号，考虑成交量放大倍数（不低于0.8）
        long_signal = long_signal * long_strength * vol_ratio.clip(0.8, None)
        short_signal = short_signal * short_strength * vol_ratio.clip(0.8, None)
        result = long_signal - short_signal
        # 归一化到[-1,1]（实际已在此范围内）
        return result.clip(-1, 1)
