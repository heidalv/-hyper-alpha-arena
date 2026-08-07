"""AI因子: 波动率调整均值回复 | 置信:60% | 计算过去N周期价格变化与波动率的比值，若比值接近0且波动率较高，则发出反转信号，避免趋势不明确时持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_Mean_Reversion(BaseFactor):
    """计算过去N周期价格变化与波动率的比值，若比值接近0且波动率较高，则发出反转信号，避免趋势不明确时持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_ma",
            name="Volatility-Adjusted Mean Reversion",
            display_name="波动率调整均值回复",
            description="计算过去N周期价格变化与波动率的比值，若比值接近0且波动率较高，则发出反转信号，避免趋势不明确时持仓。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        vol = data['volume']
        # 波动率：过去20周期ATR百分比
        atr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr_avg = atr.rolling(20).mean()
        vol_ratio = atr_avg / close * 100
        # 价格变化归一化
        ret = close.pct_change(5) * 100
        # 均值回复信号：变化小且波动率高时看反转
        signal = -np.sign(ret) * (1 - np.exp(-np.abs(ret) / (vol_ratio + 1e-8)))
        signal = signal.clip(-1, 1)
        return signal.fillna(0)
