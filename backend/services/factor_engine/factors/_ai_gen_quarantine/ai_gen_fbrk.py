"""AI因子: 假突破 | 置信:75% | 检测价格突破布林带后迅速回归带内的假突破行为，此类模式常导致master_running_close亏损或持仓超时。上轨假突破给出空头信号，下轨假突破给出多头信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class FakeBreakout(BaseFactor):
    """检测价格突破布林带后迅速回归带内的假突破行为，此类模式常导致master_running_close亏损或持仓超时。上轨假突破给出空头信号，下轨假突破给出多头信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fbrk",
            name="Fake Breakout",
            display_name="假突破",
            description="检测价格突破布林带后迅速回归带内的假突破行为，此类模式常导致master_running_close亏损或持仓超时。上轨假突破给出空头信号，下轨假突破给出多头信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        tp = (data['high'] + data['low'] + close) / 3
        middle = tp.rolling(20).mean()
        std = tp.rolling(20).std(ddof=0)
        upper = middle + 2 * std
        lower = middle - 2 * std
        prev_close = close.shift(1)
        prev_upper = upper.shift(1)
        prev_lower = lower.shift(1)
        fake_break = np.where((prev_close > prev_upper) & (close < upper), -1,
                             np.where((prev_close < prev_lower) & (close > lower), 1, 0)).astype(float)
        result = pd.Series(fake_break, index=data.index).fillna(0).clip(-1, 1)
        return result
