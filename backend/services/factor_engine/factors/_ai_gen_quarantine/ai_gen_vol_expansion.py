"""AI因子: 波动率扩张风险 | 置信:60% | 通过比较近期价格范围的扩张与价格变化方向，识别高波动率但缺乏明确趋势的环境。当波动率快速扩张而价格变化相对较小，表明市场处于无序波动，容易触发止损或超时平仓。值>0表示高扩张风险，<0表示低风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityExpansionRisk(BaseFactor):
    """通过比较近期价格范围的扩张与价格变化方向，识别高波动率但缺乏明确趋势的环境。当波动率快速扩张而价格变化相对较小，表明市场处于无序波动，容易触发止损或超时平仓。值>0表示高扩张风险，<0表示低风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_expansion",
            name="Volatility Expansion Risk",
            display_name="波动率扩张风险",
            description="通过比较近期价格范围的扩张与价格变化方向，识别高波动率但缺乏明确趋势的环境。当波动率快速扩张而价格变化相对较小，表明市场处于无序波动，容易触发止损或超时平仓。值>0表示高扩张风险，<0表示低风险。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        atr7 = tr.rolling(7).mean()
        # 价格变化率
        price_change = (close - close.shift(7)) / close.shift(7)
        # 波动率扩张比
        vol_ratio = atr7 / atr14
        # 当波动率扩张但价格变化微弱时风险高
        risk = vol_ratio * (1 - price_change.abs().clip(0, 0.2)/0.2)  # 价格变化越小，风险越高
        # 归一化到[-1,1]
        result = risk.rank(pct=True) * 2 - 1
        return result.fillna(0)
