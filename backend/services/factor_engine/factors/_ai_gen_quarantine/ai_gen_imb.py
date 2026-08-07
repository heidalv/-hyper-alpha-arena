"""AI因子: 多空失衡因子 | 置信:60% | 基于价格在日内相对位置（即从低点到高点的分位）以及成交量加权，判断是否处于极端情绪（追高或杀跌）导致的不平衡状态，此类状态容易触发止损或持仓超时亏损。计算(close-min)/(max-min)的乖离率与成交量放大程度的组合，偏离中位数越远且量越大越趋近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ImbalanceSentiment(BaseFactor):
    """基于价格在日内相对位置（即从低点到高点的分位）以及成交量加权，判断是否处于极端情绪（追高或杀跌）导致的不平衡状态，此类状态容易触发止损或持仓超时亏损。计算(close-min)/(max-min)的乖离率与成交量放大程度的组合，偏离中位数越远且量越大越趋近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_imb",
            name="ImbalanceSentiment",
            display_name="多空失衡因子",
            description="基于价格在日内相对位置（即从低点到高点的分位）以及成交量加权，判断是否处于极端情绪（追高或杀跌）导致的不平衡状态，此类状态容易触发止损或持仓超时亏损。计算(close-min)/(max-min)的乖离率与成交量放大程度的组合，偏离中位数越远且量越大越趋近-1。",
            category="behavioral",
            subcategory="sentiment",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 日内位置
        pos = (data['close'] - data['low']) / (data['high'] - data['low'] + 1e-10)
        # 偏离0.5的程度
        dev = np.abs(pos - 0.5) * 2  # [0,1]
        # 成交量相对变化
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        # 组合：高偏离+高成交量 -> 接近-1
        factor = -dev * np.clip(vol_ratio - 1, 0, None) / 2
        factor = np.clip(factor, -1, 1)
        return pd.Series(factor, index=data.index).fillna(0)
