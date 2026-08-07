"""AI因子: 波动率状态清晰度 | 置信:65% | 通过短期ATR与长期ATR的比值判断波动率是否处于不明状态。比值接近1表示波动率稳定无方向，可能触发止损，输出负值；比值远离1表示趋势明确，输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Regime_Clarity(BaseFactor):
    """通过短期ATR与长期ATR的比值判断波动率是否处于不明状态。比值接近1表示波动率稳定无方向，可能触发止损，输出负值；比值远离1表示趋势明确，输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volclarity",
            name="Volatility Regime Clarity",
            display_name="波动率状态清晰度",
            description="通过短期ATR与长期ATR的比值判断波动率是否处于不明状态。比值接近1表示波动率稳定无方向，可能触发止损，输出负值；比值远离1表示趋势明确，输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # ATR计算
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr_short = tr.rolling(window=5).mean()
        atr_long = tr.rolling(window=20).mean()
        ratio = atr_short / atr_long
        # 映射到[-1,1]: 比值偏离1越多，信号越强；用(ratio-1)经tanh缩放
        signal = np.tanh((ratio - 1) * 4)  # 乘4放大
        return signal.fillna(0)
