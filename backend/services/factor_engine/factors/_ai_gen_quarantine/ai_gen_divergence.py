"""AI因子: 量价背离指数 | 置信:60% | 检测价格与成交量的短期与中期背离程度，背离越大表示趋势不可靠，可能处于未知状态，输出-1（强背离/unknown）、0（无信号）、1（量价齐升/趋势明确）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """检测价格与成交量的短期与中期背离程度，背离越大表示趋势不可靠，可能处于未知状态，输出-1（强背离/unknown）、0（无信号）、1（量价齐升/趋势明确）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_divergence",
            name="Volume-Price Divergence",
            display_name="量价背离指数",
            description="检测价格与成交量的短期与中期背离程度，背离越大表示趋势不可靠，可能处于未知状态，输出-1（强背离/unknown）、0（无信号）、1（量价齐升/趋势明确）。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 输入data: pd.DataFrame with OHLCV
        close = data['close']
        volume = data['volume']
        # 短期动量 (价格变化)
        ret_short = close.pct_change(3)
        vol_short = volume.pct_change(3)
        # 中期动量
        ret_med = close.pct_change(10)
        vol_med = volume.pct_change(10)
        # 背离指标: 价格与成交量符号差异
        div_short = (ret_short > 0) & (vol_short < 0) | (ret_short < 0) & (vol_short > 0)
        div_med = (ret_med > 0) & (vol_med < 0) | (ret_med < 0) & (vol_med > 0)
        # 综合背离得分
        divergence = (div_short.astype(int) + div_med.astype(int)).clip(0,2)
        # 映射到[-1,1]: 2 -> -1, 1 -> 0, 0 -> 1
        result = pd.Series(0.0, index=data.index)
        result[divergence == 2] = -1.0
        result[divergence == 1] = 0.0
        result[divergence == 0] = 1.0
        return result
