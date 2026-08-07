"""AI因子: 效率趋势因子 | 置信:60% | 基于效率系数（价格净变化与总波动的比值）衡量趋势强度与方向。效率系数高表示趋势清晰，低表示震荡无序。在震荡市场（效率系数低）中做多容易产生max_hold_timeout和master_running亏损。输出值[-1,1]，正值表示强上升趋势，负值表示强下降趋势，绝对值小表示震荡。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Efficiency_Ratio_Trend_Indicator(BaseFactor):
    """基于效率系数（价格净变化与总波动的比值）衡量趋势强度与方向。效率系数高表示趋势清晰，低表示震荡无序。在震荡市场（效率系数低）中做多容易产生max_hold_timeout和master_running亏损。输出值[-1,1]，正值表示强上升趋势，负值表示强下降趋势，绝对值小表示震荡。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_eff_trend",
            name="Efficiency Ratio Trend Indicator",
            display_name="效率趋势因子",
            description="基于效率系数（价格净变化与总波动的比值）衡量趋势强度与方向。效率系数高表示趋势清晰，低表示震荡无序。在震荡市场（效率系数低）中做多容易产生max_hold_timeout和master_running亏损。输出值[-1,1]，正值表示强上升趋势，负值表示强下降趋势，绝对值小表示震荡。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        lookback = 20
        close = data['close']
        # 计算效率系数：|close - close_shift| / sum(|returns|)
        direction = close.diff(lookback).abs()
        total_volatility = close.diff().abs().rolling(lookback).sum()
        # 避免除零
        eff_ratio = np.where(total_volatility != 0, direction / total_volatility, 0)
        # 符号：当前收盘价相对于lookback前的方向
        sign = np.sign(close - close.shift(lookback))
        result = pd.Series(eff_ratio * sign, index=data.index)
        # 使用滚动窗口防止未来信息，并填充NaN
        result = result.fillna(0).clip(-1, 1)
        return result
