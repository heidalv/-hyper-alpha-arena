"""AI因子: 极值范围反转 | 置信:60% | 当价格在短期（如5周期）内创出最高或最低，且波动率（ATR）同时达到近期极值，后续价格向反方向移动的概率较高。该因子利用价格极值和波动率极值的叠加作为反转信号。计算当前价格是否为近期最高/最低，同时检查ATR是否处于90%分位数以上，然后输出方向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ExtremeRangeReversal(BaseFactor):
    """当价格在短期（如5周期）内创出最高或最低，且波动率（ATR）同时达到近期极值，后续价格向反方向移动的概率较高。该因子利用价格极值和波动率极值的叠加作为反转信号。计算当前价格是否为近期最高/最低，同时检查ATR是否处于90%分位数以上，然后输出方向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_range_reversal",
            name="Extreme Range Reversal",
            display_name="极值范围反转",
            description="当价格在短期（如5周期）内创出最高或最低，且波动率（ATR）同时达到近期极值，后续价格向反方向移动的概率较高。该因子利用价格极值和波动率极值的叠加作为反转信号。计算当前价格是否为近期最高/最低，同时检查ATR是否处于90%分位数以上，然后输出方向信号。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(window=14).mean()
        atr_pct = atr / atr.rolling(window=50).quantile(0.9)  # 相对90%分位
        # 短期极值
        short_high = high.rolling(window=5).max()
        short_low = low.rolling(window=5).min()
        cond_high = (close == short_high) & (atr_pct > 1.0)
        cond_low = (close == short_low) & (atr_pct > 1.0)
        signal = np.where(cond_high, -1.0, np.where(cond_low, 1.0, 0.0))
        return pd.Series(signal, index=data.index)
