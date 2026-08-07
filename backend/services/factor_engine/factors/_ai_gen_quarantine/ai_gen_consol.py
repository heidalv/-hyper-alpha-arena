"""AI因子: 盘整检测因子 | 置信:55% | 通过比较近期价格高低范围与波动率比值，识别市场处于窄幅横盘状态，此时趋势策略容易亏损。因子值接近-1表示高度盘整，+1表示趋势明确。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Consolidation_Detection(BaseFactor):
    """通过比较近期价格高低范围与波动率比值，识别市场处于窄幅横盘状态，此时趋势策略容易亏损。因子值接近-1表示高度盘整，+1表示趋势明确。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_consol",
            name="Consolidation Detection",
            display_name="盘整检测因子",
            description="通过比较近期价格高低范围与波动率比值，识别市场处于窄幅横盘状态，此时趋势策略容易亏损。因子值接近-1表示高度盘整，+1表示趋势明确。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 20日高低范围
        range_20 = high.rolling(20).max() - low.rolling(20).min()
        # 20日ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr_20 = tr.rolling(20).mean()
        # 范围与ATR的比值，大比值表示大区间突破，小比值表示窄幅盘整
        ratio = range_20 / atr_20
        # 取倒数？实际盘整时比值接近1，趋势放大时比值>1，用对数
        log_ratio = np.log(ratio.clip(lower=0.01))
        # 归一化到[-1,1]
        mean = log_ratio.rolling(60).mean()
        std = log_ratio.rolling(60).std()
        z = (log_ratio - mean) / std
        result = np.clip(z, -2, 2) / 2.0
        return result
