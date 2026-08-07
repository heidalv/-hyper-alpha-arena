"""AI因子: 波动率价格背离因子 | 置信:65% | 衡量短期波动率扩张与价格方向的关系。当波动率急剧上升但价格下跌，表明市场恐慌或抛压增大，做多风险极高。因子值接近-1指示卖出信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Price_Divergence_Factor(BaseFactor):
    """衡量短期波动率扩张与价格方向的关系。当波动率急剧上升但价格下跌，表明市场恐慌或抛压增大，做多风险极高。因子值接近-1指示卖出信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price",
            name="Volatility Price Divergence Factor",
            display_name="波动率价格背离因子",
            description="衡量短期波动率扩张与价格方向的关系。当波动率急剧上升但价格下跌，表明市场恐慌或抛压增大，做多风险极高。因子值接近-1指示卖出信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算真实波幅ATR（14日）
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr14 = tr.rolling(14).mean()
        # 短期ATR变化率（3日扩张）
        atr_change = atr14.pct_change(3)
        # 价格短期变动（3日收益率）
        ret3 = close.pct_change(3)
        # 波动率扩张且价格下跌时，信号为负
        raw = -np.sign(atr_change) * np.sign(ret3) * np.abs(atr_change) * np.abs(ret3) * 100
        # 缩放至[-1,1]
        result = np.tanh(raw).clip(-1, 1)
        return result
