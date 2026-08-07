"""AI因子: 趋势效率衰减因子 | 置信:60% | 计算近期价格效率（净位移/总波幅）与更长回溯期的效率差值，反映趋势质量恶化，当效率急剧下降时返回负值，预示趋势衰竭可能导致持仓超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendEfficiencyFading(BaseFactor):
    """计算近期价格效率（净位移/总波幅）与更长回溯期的效率差值，反映趋势质量恶化，当效率急剧下降时返回负值，预示趋势衰竭可能导致持仓超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tef",
            name="Trend Efficiency Fading",
            display_name="趋势效率衰减因子",
            description="计算近期价格效率（净位移/总波幅）与更长回溯期的效率差值，反映趋势质量恶化，当效率急剧下降时返回负值，预示趋势衰竭可能导致持仓超时亏损。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 计算价格变动
        delta = close.diff()
        # 近期效率：10周期净位移 / 10周期路径长度
        n_short = 10
        net_move_short = (close - close.shift(n_short)).abs()
        path_short = delta.abs().rolling(n_short).sum()
        efficiency_short = net_move_short / path_short.replace(0, np.nan)
        # 长期效率：30周期
        n_long = 30
        net_move_long = (close - close.shift(n_long)).abs()
        path_long = delta.abs().rolling(n_long).sum()
        efficiency_long = net_move_long / path_long.replace(0, np.nan)
        # 效率差
        eff_diff = efficiency_short - efficiency_long
        # 归一化到[-1,1]：使用滚动排名百分位
        rank = eff_diff.rolling(50).rank(pct=True) * 2 - 1
        result = rank.clip(-1, 1)
        return result
