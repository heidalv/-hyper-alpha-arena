"""AI因子: 波动率风险调整因子 | 置信:60% | 基于价格波动率的变化率（ATR比率）来识别市场状态突变风险。当ATR在短期窗口内急剧上升时，表明市场进入未知高波动状态，此时持仓风险增加，应给予负向信号。通过比较当前ATR与过去N周期中位数，计算偏离程度，映射到[-1,1]区间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRiskAdjustment(BaseFactor):
    """基于价格波动率的变化率（ATR比率）来识别市场状态突变风险。当ATR在短期窗口内急剧上升时，表明市场进入未知高波动状态，此时持仓风险增加，应给予负向信号。通过比较当前ATR与过去N周期中位数，计算偏离程度，映射到[-1,1]区间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vra",
            name="Volatility Risk Adjustment",
            display_name="波动率风险调整因子",
            description="基于价格波动率的变化率（ATR比率）来识别市场状态突变风险。当ATR在短期窗口内急剧上升时，表明市场进入未知高波动状态，此时持仓风险增加，应给予负向信号。通过比较当前ATR与过去N周期中位数，计算偏离程度，映射到[-1,1]区间。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # ATR计算
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        # ATR中位数（过去60周期）
        atr_med = atr.rolling(60).median()
        # 相对偏离
        deviation = (atr - atr_med) / (atr_med + 1e-10)
        # 映射到[-1,1]，使用tanh裁剪
        result = -np.tanh(deviation * 3)  # 负号：高波动风险信号为负
        return result.fillna(0.0)
