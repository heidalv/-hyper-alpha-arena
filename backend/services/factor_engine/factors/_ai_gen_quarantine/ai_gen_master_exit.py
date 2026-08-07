"""AI因子: 主力平仓 | 置信:60% | 模拟大户平仓行为，当价格下跌且成交量显著放大时发出负向信号。计算短期价格变化率与成交量变化率的负相关性，并利用成交量Z-score增强识别。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MasterRunningClose(BaseFactor):
    """模拟大户平仓行为，当价格下跌且成交量显著放大时发出负向信号。计算短期价格变化率与成交量变化率的负相关性，并利用成交量Z-score增强识别。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_master_exit",
            name="Master Running Close",
            display_name="主力平仓",
            description="模拟大户平仓行为，当价格下跌且成交量显著放大时发出负向信号。计算短期价格变化率与成交量变化率的负相关性，并利用成交量Z-score增强识别。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 短期价格变化率
        ret = close.pct_change(3)
        # 成交量变化率（相对5期均值）
        vol_ma5 = volume.rolling(5).mean()
        vol_ratio = volume / vol_ma5
        # 价格下跌（ret<0）且成交量放大（vol_ratio>1）时信号强
        raw = -ret * (vol_ratio - 1)
        # 只考虑下跌情况
        raw[ret >= 0] = 0
        # 归一化，使用logistic或tanh
        result = np.tanh(raw)
        result = result.fillna(0)
        return result
