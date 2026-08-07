"""AI因子: 市场清晰度指数 | 置信:65% | 衡量当前市场是否具有明确趋势。通过比较短期价格趋势的斜率与波动率的比值，若比值高则趋势清晰（正因子），比值低则市场震荡（负因子）。在regime=unknown时，该因子可过滤掉震荡行情中的无效信号，减少因方向不明导致的止损和超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketClarityIndex(BaseFactor):
    """衡量当前市场是否具有明确趋势。通过比较短期价格趋势的斜率与波动率的比值，若比值高则趋势清晰（正因子），比值低则市场震荡（负因子）。在regime=unknown时，该因子可过滤掉震荡行情中的无效信号，减少因方向不明导致的止损和超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mci",
            name="Market Clarity Index",
            display_name="市场清晰度指数",
            description="衡量当前市场是否具有明确趋势。通过比较短期价格趋势的斜率与波动率的比值，若比值高则趋势清晰（正因子），比值低则市场震荡（负因子）。在regime=unknown时，该因子可过滤掉震荡行情中的无效信号，减少因方向不明导致的止损和超时亏损。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        period = 20
        # 计算线性趋势斜率（使用线性回归，取最近20根）
        x = np.arange(period)
        y = close.tail(period).values
        if len(y) < period:
            return pd.Series(0.0, index=close.index)
        slope = np.polyfit(x, y, 1)[0]
        # 波动率：平均真实波幅（ATR）归一化
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(window=period).mean().iloc[-1]
        if atr == 0:
            return pd.Series(0.0, index=close.index)
        # 归一化斜率到[-1,1]，使用atanh或者简单clamp
        raw = slope / atr
        # 用tanh将值映射到[-1,1]
        result = np.tanh(raw * 10)  # 乘10放大差异
        return pd.Series(result, index=close.index)
