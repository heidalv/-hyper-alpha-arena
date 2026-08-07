"""AI因子: 波动率突变因子 | 置信:65% | 通过ATR变化率识别波动率由高转低的震荡收缩阶段，此时易触发止损或止盈亏损。当短期ATR相比长期ATR显著下降时输出负向信号，建议避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Regime_Change(BaseFactor):
    """通过ATR变化率识别波动率由高转低的震荡收缩阶段，此时易触发止损或止盈亏损。当短期ATR相比长期ATR显著下降时输出负向信号，建议避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_change",
            name="Volatility Regime Change",
            display_name="波动率突变因子",
            description="通过ATR变化率识别波动率由高转低的震荡收缩阶段，此时易触发止损或止盈亏损。当短期ATR相比长期ATR显著下降时输出负向信号，建议避免做多。",
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
        # 计算TR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(20).mean()
        # 波动率变化率 (短期/长期 - 1)
        ratio = atr_short / atr_long - 1.0
        # 映射到[-1,1]，当ratio负值较大时表示波动率收缩，信号为负
        result = np.clip(ratio * -10, -1, 1)
        return result
