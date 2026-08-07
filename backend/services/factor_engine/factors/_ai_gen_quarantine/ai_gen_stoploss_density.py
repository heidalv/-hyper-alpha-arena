"""AI因子: 止损触发密度 | 置信:60% | 基于价格连续突破布林带上下轨的次数，衡量近期止损被频繁触发的概率。当价格经常穿越布林带边界时，表明市场噪声大，容易触发止损。因子值接近-1表示高止损密度（风险高），+1表示低止损密度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossTriggerDensity(BaseFactor):
    """基于价格连续突破布林带上下轨的次数，衡量近期止损被频繁触发的概率。当价格经常穿越布林带边界时，表明市场噪声大，容易触发止损。因子值接近-1表示高止损密度（风险高），+1表示低止损密度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stoploss_density",
            name="Stop-Loss Trigger Density",
            display_name="止损触发密度",
            description="基于价格连续突破布林带上下轨的次数，衡量近期止损被频繁触发的概率。当价格经常穿越布林带边界时，表明市场噪声大，容易触发止损。因子值接近-1表示高止损密度（风险高），+1表示低止损密度。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 布林带 (20周期，2倍标准差)
        sma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        # 标记突破
        break_upper = (high > upper).astype(int)
        break_lower = (low < lower).astype(int)
        # 统计过去N周期突破次数
        n = 10
        density = (break_upper.rolling(n).sum() + break_lower.rolling(n).sum()) / n
        # 归一化到[-1,1]：密度0->1, 密度1->-1
        result = 1 - 2 * density.clip(0, 1)
        result = result.fillna(0)
        return result
