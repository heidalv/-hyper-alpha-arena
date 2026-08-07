"""AI因子: 波动率压缩百分位因子 | 置信:60% | 计算布林带宽度在历史窗口中的百分位。宽度低位表示波动率压缩，市场处于震荡状态，易触发持仓超时；宽度高位表示趋势扩张。因子值-1（极度压缩）到+1（极度扩张），建议在压缩区降低交易倾向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySqueezePercentile(BaseFactor):
    """计算布林带宽度在历史窗口中的百分位。宽度低位表示波动率压缩，市场处于震荡状态，易触发持仓超时；宽度高位表示趋势扩张。因子值-1（极度压缩）到+1（极度扩张），建议在压缩区降低交易倾向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volsq",
            name="Volatility Squeeze Percentile",
            display_name="波动率压缩百分位因子",
            description="计算布林带宽度在历史窗口中的百分位。宽度低位表示波动率压缩，市场处于震荡状态，易触发持仓超时；宽度高位表示趋势扩张。因子值-1（极度压缩）到+1（极度扩张），建议在压缩区降低交易倾向。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        period = 20
        window_hist = 100
        close = data['close']
        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        bb_width = 2 * std / (sma + 1e-9)
        percentile = bb_width.rolling(window_hist).apply(lambda x: (x < x.iloc[-1]).mean(), raw=False)
        result = 2 * percentile - 1
        return result
